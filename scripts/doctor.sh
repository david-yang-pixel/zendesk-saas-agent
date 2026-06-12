#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEFAULT_ENV_FILE="/opt/zd-agent/.env"
if [[ -z "${ENV_FILE:-}" ]]; then
  if [[ -f "$DEFAULT_ENV_FILE" ]]; then
    ENV_FILE="$DEFAULT_ENV_FILE"
  elif [[ -f "$ROOT_DIR/.env" ]]; then
    ENV_FILE="$ROOT_DIR/.env"
  else
    ENV_FILE="$DEFAULT_ENV_FILE"
  fi
fi

FAILURES=0
WARNINGS=0

ok() {
  printf 'OK   %s\n' "$*"
}

warn() {
  WARNINGS=$((WARNINGS + 1))
  printf 'WARN %s\n' "$*"
}

fail() {
  FAILURES=$((FAILURES + 1))
  printf 'FAIL %s\n' "$*"
}

info() {
  printf 'INFO %s\n' "$*"
}

require_cmd() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    ok "command available: $name"
  else
    fail "command missing: $name"
  fi
}

load_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    fail "env file not found: $ENV_FILE"
    return
  fi

  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  ok "loaded env file: $ENV_FILE"
}

require_env() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then
    ok "env configured: $key"
  else
    fail "env missing: $key"
  fi
}

optional_env() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then
    ok "optional env configured: $key"
  else
    warn "optional env missing: $key"
  fi
}

curl_direct() {
  curl --noproxy "*" "$@"
}

short_error() {
  python3 - "$1" <<'PY'
import sys
text = sys.argv[1].replace("\n", " ").strip()
print(text[:240])
PY
}

json_tool_names() {
  python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(2)

result = data.get("result") if isinstance(data, dict) else None
if not isinstance(result, dict):
    sys.exit(3)

tools = result.get("tools")
if not isinstance(tools, list):
    sys.exit(4)

names = []
for item in tools:
    if isinstance(item, dict) and item.get("name"):
        names.append(str(item["name"]))
print(",".join(names))
'
}

mcp_endpoint() {
  local base="${1%/}"
  if [[ -z "$base" ]]; then
    printf ''
  elif [[ "$base" == */mcp || "$base" == */zendesk-reply-mcp ]]; then
    printf '%s' "$base"
  else
    printf '%s/mcp' "$base"
  fi
}

mcp_tools_list() {
  local label="$1"
  local url="$2"
  local api_key="$3"
  local expected_tool="$4"
  local timeout="${5:-10}"

  if [[ -z "$url" || -z "$api_key" ]]; then
    fail "$label MCP not configured"
    return
  fi

  local payload='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
  local body
  local status
  local tmp
  tmp="$(mktemp)"
  status="$(
    curl_direct -sS -m "$timeout" \
      -o "$tmp" \
      -w '%{http_code}' \
      -H "Authorization: Bearer $api_key" \
      -H 'Accept: application/json, text/event-stream' \
      -H 'Content-Type: application/json' \
      -d "$payload" \
      "$url" 2>"$tmp.err" || true
  )"
  body="$(cat "$tmp" 2>/dev/null || true)"

  if [[ "$status" != 2* ]]; then
    local err
    err="$(cat "$tmp.err" 2>/dev/null || true)"
    [[ -z "$err" ]] && err="$body"
    fail "$label MCP tools/list failed: HTTP ${status:-curl_error} $(short_error "$err")"
    rm -f "$tmp" "$tmp.err"
    return
  fi

  local tools
  if ! tools="$(printf '%s' "$body" | json_tool_names 2>/dev/null)"; then
    fail "$label MCP tools/list returned unexpected response"
    rm -f "$tmp" "$tmp.err"
    return
  fi

  if [[ ",$tools," == *",$expected_tool,"* ]]; then
    ok "$label MCP exposes tool: $expected_tool"
  else
    fail "$label MCP missing expected tool: $expected_tool (tools: ${tools:-none})"
  fi
  rm -f "$tmp" "$tmp.err"
}

check_http_ok() {
  local label="$1"
  local url="$2"
  local timeout="${3:-5}"
  local status
  status="$(curl_direct -sS -m "$timeout" -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)"
  if [[ "$status" == 2* ]]; then
    ok "$label reachable: $url"
  else
    fail "$label unreachable: $url (HTTP ${status:-curl_error})"
  fi
}

check_db_gateway_status() {
  local base="${DB_GATEWAY_URL:-}"
  local key="${DB_GATEWAY_API_KEY:-}"
  if [[ -z "$base" || -z "$key" ]]; then
    fail "DB Gateway status not configured"
    return
  fi

  local status_url="${base%/}/status"
  local status
  status="$(
    curl_direct -sS -m 10 \
      -o /dev/null \
      -w '%{http_code}' \
      -H "X-API-Key: $key" \
      "$status_url" 2>/dev/null || true
  )"
  if [[ "$status" == 2* ]]; then
    ok "DB Gateway status reachable"
  else
    warn "DB Gateway status check failed: $status_url (HTTP ${status:-curl_error})"
  fi
}

check_systemd_unit() {
  local unit="$1"
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl unavailable; skip unit check: $unit"
    return
  fi
  if systemctl is-active --quiet "$unit" 2>/dev/null; then
    ok "systemd active: $unit"
  else
    warn "systemd not active or unavailable: $unit"
  fi
}

check_zd_agent_health() {
  local health_url="${ZD_AGENT_HEALTH_URL:-http://127.0.0.1:8080/healthz}"
  check_http_ok "zd-agent health" "$health_url" 5
}

check_browser_relay() {
  local base="${ZENDESK_BROWSER_RELAY_URL:-}"
  local key="${ZENDESK_BROWSER_RELAY_API_KEY:-}"
  if [[ -z "$base" || -z "$key" ]]; then
    fail "Zendesk browser relay not configured"
    return
  fi

  local lowered="${base%/}"
  if [[ "$lowered" == */mcp || "$lowered" == */zendesk-reply-mcp ]]; then
    mcp_tools_list "Zendesk browser relay" "$lowered" "$key" "send_zendesk_native_message" 10
    return
  fi

  check_http_ok "Zendesk browser relay health" "${lowered}/health" 5
  warn "Zendesk browser relay URL is legacy HTTP mode; agent sends through /send, not remote MCP"
}

main() {
  printf 'zd-agent doctor\n'
  printf 'repo: %s\n' "$ROOT_DIR"
  printf 'env:  %s\n\n' "$ENV_FILE"

  require_cmd curl
  require_cmd python3
  if command -v git >/dev/null 2>&1; then
    local branch
    branch="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    [[ -n "$branch" ]] && info "git branch: $branch"
  else
    warn "command missing: git"
  fi

  printf '\n[env]\n'
  load_env_file
  require_env ZENDESK_SUBDOMAIN
  require_env ZENDESK_EMAIL
  require_env ZENDESK_API_TOKEN
  require_env ZENDESK_WEBHOOK_SECRET
  require_env DB_GATEWAY_URL
  require_env DB_GATEWAY_API_KEY
  require_env SIGNOZ_MCP_URL
  require_env SIGNOZ_MCP_API_KEY
  require_env ZENDESK_BROWSER_RELAY_URL
  require_env ZENDESK_BROWSER_RELAY_API_KEY
  optional_env HC_CLIENT_URL
  optional_env HC_CLIENT_API_KEY

  printf '\n[local services]\n'
  check_systemd_unit zd-agent
  check_zd_agent_health

  printf '\n[remote tools]\n'
  check_db_gateway_status
  mcp_tools_list "DB Gateway" "$(mcp_endpoint "${DB_GATEWAY_URL:-}")" "${DB_GATEWAY_API_KEY:-}" "db_gateway_query" 15
  mcp_tools_list "SigNoz" "$(mcp_endpoint "${SIGNOZ_MCP_URL:-}")" "${SIGNOZ_MCP_API_KEY:-}" "sz_run" 15
  check_browser_relay

  printf '\n[log reporter]\n'
  if [[ -n "${HC_CLIENT_URL:-}" && -n "${HC_CLIENT_API_KEY:-}" ]]; then
    ok "SaaS run log reporter configured"
  else
    warn "SaaS run log reporter disabled; set HC_CLIENT_URL and HC_CLIENT_API_KEY"
  fi

  printf '\nsummary: %s failure(s), %s warning(s)\n' "$FAILURES" "$WARNINGS"
  if [[ "$FAILURES" -gt 0 ]]; then
    exit 1
  fi
}

main "$@"
