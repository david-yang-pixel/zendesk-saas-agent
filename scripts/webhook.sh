#!/usr/bin/env bash

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
LOG_FILE="${LOG_DIR}/webhook.log"
PID_FILE="${LOG_DIR}/webhook.pid"
STATE_FILE="${LOG_DIR}/webhook.state"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
DEFAULT_HOST="0.0.0.0"
DEFAULT_PORT="8080"

HOST="${DEFAULT_HOST}"
PORT="${DEFAULT_PORT}"
HOST_SET=0
PORT_SET=0

print_usage() {
  cat <<'EOF'
用法:
  bash scripts/webhook.sh check
  bash scripts/webhook.sh start [--host 0.0.0.0] [--port 8080]
  bash scripts/webhook.sh stop
  bash scripts/webhook.sh status
  bash scripts/webhook.sh restart [--host 0.0.0.0] [--port 8080]
  bash scripts/webhook.sh logs
EOF
}

log() {
  printf '[webhookctl] %s\n' "$1"
}

error() {
  printf '[webhookctl] 错误: %s\n' "$1" >&2
}

ensure_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    error "未检测到 uv，请先安装 uv"
    exit 1
  fi
}

ensure_project_root() {
  if [[ ! -f "${ROOT_DIR}/pyproject.toml" ]]; then
    error "未在项目根目录找到 pyproject.toml"
    exit 1
  fi
}

ensure_log_dir() {
  mkdir -p "${LOG_DIR}"
}

ensure_venv() {
  if [[ -d "${ROOT_DIR}/.venv" ]]; then
    log "已检测到 .venv"
    return
  fi

  log "未检测到 .venv，开始执行 uv sync"
  (
    cd "${ROOT_DIR}" &&
    uv sync
  ) || {
    error "uv sync 执行失败"
    exit 1
  }
}

ensure_python_bin() {
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    error "未找到可执行的 ${PYTHON_BIN}，请先检查 uv sync 是否成功"
    exit 1
  fi
}

is_running() {
  local pid="$1"
  kill -0 "${pid}" >/dev/null 2>&1
}

find_listening_pid() {
  local port="$1"
  local port_pid=""

  if command -v ss >/dev/null 2>&1; then
    port_pid="$(
      ss -ltnp "sport = :${port}" 2>/dev/null \
        | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
        | head -n 1
    )"
    if [[ -n "${port_pid}" ]]; then
      printf '%s\n' "${port_pid}"
      return
    fi
  fi

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null | head -n 1
  fi
}

find_child_pid() {
  local pid="$1"

  ps -o pid= --ppid "${pid}" 2>/dev/null | awk 'NF { print $1; exit }'
}

find_deepest_child_pid() {
  local root_pid="$1"
  local current_pid="$1"
  local next_pid=""

  while true; do
    next_pid="$(find_child_pid "${current_pid}")"
    if [[ -z "${next_pid}" ]]; then
      break
    fi
    current_pid="${next_pid}"
  done

  if [[ "${current_pid}" == "${root_pid}" ]]; then
    return
  fi

  printf '%s\n' "${current_pid}"
}

read_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    tr -d '[:space:]' < "${PID_FILE}"
  fi
}

load_state() {
  local state_host=""
  local state_port=""

  if [[ ! -f "${STATE_FILE}" ]]; then
    return
  fi

  # shellcheck disable=SC1090
  source "${STATE_FILE}"

  if [[ "${HOST_SET}" -eq 0 && -n "${STATE_HOST:-}" ]]; then
    HOST="${STATE_HOST}"
  fi
  if [[ "${PORT_SET}" -eq 0 && -n "${STATE_PORT:-}" ]]; then
    PORT="${STATE_PORT}"
  fi
}

write_state() {
  cat > "${STATE_FILE}" <<EOF
STATE_HOST=${HOST}
STATE_PORT=${PORT}
EOF
}

cleanup_stale_pid() {
  local pid
  pid="$(read_pid)"

  if [[ -z "${pid}" ]]; then
    rm -f "${PID_FILE}"
    return
  fi

  if ! is_running "${pid}"; then
    log "发现失效的 PID 文件，已清理"
    rm -f "${PID_FILE}"
  fi
}

check_env() {
  ensure_project_root
  ensure_uv

  if [[ -d "${ROOT_DIR}/.venv" ]]; then
    log "uv 已安装，.venv 已存在"
  else
    log "uv 已安装，但 .venv 不存在"
  fi
}

start_webhook() {
  local pid
  local launcher_pid
  local port_pid

  ensure_project_root
  ensure_uv
  ensure_log_dir
  cleanup_stale_pid

  pid="$(read_pid)"
  if [[ -n "${pid}" ]] && is_running "${pid}"; then
    error "Webhook 已在运行中，PID=${pid}"
    exit 1
  fi

  ensure_venv
  ensure_python_bin

  write_state

  port_pid="$(find_listening_pid "${PORT}")"
  if [[ -n "${port_pid}" ]]; then
    error "端口 ${PORT} 已被占用，PID=${port_pid}"
    exit 1
  fi

  log "开始后台启动 Webhook，host=${HOST} port=${PORT}"
  (
    cd "${ROOT_DIR}" &&
    nohup "${PYTHON_BIN}" -m zd_agent.entry.cli webhook --host "${HOST}" --port "${PORT}" >> "${LOG_FILE}" 2>&1 &
    echo $! > "${PID_FILE}"
  )

  launcher_pid="$(read_pid)"
  sleep 2

  pid="$(find_listening_pid "${PORT}")"
  if [[ -z "${pid}" ]]; then
    pid="$(find_deepest_child_pid "${launcher_pid}")"
  fi
  if [[ -z "${pid}" ]] && [[ -n "${launcher_pid}" ]] && is_running "${launcher_pid}"; then
    pid="${launcher_pid}"
  fi

  if [[ -z "${pid}" ]] || ! is_running "${pid}"; then
    error "Webhook 启动失败，请查看日志 ${LOG_FILE}"
    rm -f "${PID_FILE}"
    if [[ -f "${LOG_FILE}" ]]; then
      tail -n 20 "${LOG_FILE}" || true
    fi
    exit 1
  fi

  printf '%s\n' "${pid}" > "${PID_FILE}"
  write_state

  log "Webhook 已启动，PID=${pid}"
  log "日志文件: ${LOG_FILE}"
}

stop_webhook() {
  local pid
  local waited=0

  ensure_log_dir
  cleanup_stale_pid
  pid="$(read_pid)"

  if [[ -z "${pid}" ]]; then
    log "Webhook 当前未运行"
    return
  fi

  log "正在停止 Webhook，PID=${pid}"
  kill "${pid}" >/dev/null 2>&1 || true

  while is_running "${pid}" && [[ "${waited}" -lt 10 ]]; do
    sleep 1
    waited=$((waited + 1))
  done

  if is_running "${pid}"; then
    log "普通停止超时，执行强制终止"
    kill -9 "${pid}" >/dev/null 2>&1 || true
    sleep 1
  fi

  if is_running "${pid}"; then
    error "Webhook 进程仍未退出，请手动检查 PID=${pid}"
    exit 1
  fi

  rm -f "${PID_FILE}"
  log "Webhook 已停止"
}

status_webhook() {
  local pid
  local port_pid

  ensure_log_dir
  cleanup_stale_pid
  pid="$(read_pid)"

  if [[ -n "${pid}" ]] && is_running "${pid}"; then
    log "Webhook 运行中，host=${HOST}，port=${PORT}，PID=${pid}，日志=${LOG_FILE}"
    return
  fi

  port_pid="$(find_listening_pid "${PORT}")"
  if [[ -n "${port_pid}" ]]; then
    log "Webhook PID 文件不存在，但端口 ${PORT} 仍被进程 ${port_pid} 占用"
    return
  fi

  log "Webhook 未运行"
}

logs_webhook() {
  ensure_log_dir
  touch "${LOG_FILE}"
  tail -f "${LOG_FILE}"
}

parse_options() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --host)
        HOST="$2"
        HOST_SET=1
        shift 2
        ;;
      --port)
        PORT="$2"
        PORT_SET=1
        shift 2
        ;;
      *)
        error "未知参数: $1"
        print_usage
        exit 1
        ;;
    esac
  done
}

ACTION="${1:-}"

if [[ -z "${ACTION}" ]]; then
  print_usage
  exit 1
fi

shift || true

case "${ACTION}" in
  check)
    parse_options "$@"
    check_env
    ;;
  start)
    parse_options "$@"
    load_state
    start_webhook
    ;;
  stop)
    parse_options "$@"
    load_state
    stop_webhook
    ;;
  status)
    parse_options "$@"
    load_state
    status_webhook
    ;;
  restart)
    parse_options "$@"
    load_state
    stop_webhook
    start_webhook
    ;;
  logs)
    parse_options "$@"
    load_state
    logs_webhook
    ;;
  *)
    error "未知操作: ${ACTION}"
    print_usage
    exit 1
    ;;
esac
