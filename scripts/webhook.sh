#!/usr/bin/env bash

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
LOG_FILE="${LOG_DIR}/webhook.log"
PID_FILE="${LOG_DIR}/webhook.pid"
DEFAULT_HOST="0.0.0.0"
DEFAULT_PORT="8080"

HOST="${DEFAULT_HOST}"
PORT="${DEFAULT_PORT}"

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

is_running() {
  local pid="$1"
  kill -0 "${pid}" >/dev/null 2>&1
}

read_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    tr -d '[:space:]' < "${PID_FILE}"
  fi
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

  log "开始后台启动 Webhook，host=${HOST} port=${PORT}"
  (
    cd "${ROOT_DIR}" &&
    nohup uv run zd-agent webhook --host "${HOST}" --port "${PORT}" >> "${LOG_FILE}" 2>&1 &
    echo $! > "${PID_FILE}"
  )

  pid="$(read_pid)"
  sleep 2

  if [[ -z "${pid}" ]] || ! is_running "${pid}"; then
    error "Webhook 启动失败，请查看日志 ${LOG_FILE}"
    rm -f "${PID_FILE}"
    if [[ -f "${LOG_FILE}" ]]; then
      tail -n 20 "${LOG_FILE}" || true
    fi
    exit 1
  fi

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

  ensure_log_dir
  cleanup_stale_pid
  pid="$(read_pid)"

  if [[ -n "${pid}" ]] && is_running "${pid}"; then
    log "Webhook 运行中，PID=${pid}，日志=${LOG_FILE}"
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
        shift 2
        ;;
      --port)
        PORT="$2"
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
    start_webhook
    ;;
  stop)
    parse_options "$@"
    stop_webhook
    ;;
  status)
    parse_options "$@"
    status_webhook
    ;;
  restart)
    parse_options "$@"
    stop_webhook
    start_webhook
    ;;
  logs)
    parse_options "$@"
    logs_webhook
    ;;
  *)
    error "未知操作: ${ACTION}"
    print_usage
    exit 1
    ;;
esac
