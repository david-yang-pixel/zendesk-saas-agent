#!/usr/bin/env bash
# Expose only the authenticated Greenfield Admin console on the formal domain.

set -euo pipefail
umask 077

if [[ "$EUID" -ne 0 ]]; then
  echo "Admin route switch must run as root" >&2
  exit 1
fi

CODE_DIR="${ZD_AGENT_CODE_DIR:-/opt/zd-agent/code}"
STATE_DIR="${CASEWORK_STATE_DIR:-/opt/zd-agent/greenfield}"
NGINX_CONFIG="${CASEWORK_NGINX_CONFIG:-/etc/nginx/sites-enabled/zd-agent.conf}"
BACKUP="$STATE_DIR/nginx.pre-greenfield-admin.conf"
LOCK_FILE="${ZD_AGENT_DEPLOY_LOCK_FILE:-/run/lock/zd-agent-deploy.lock}"

exec 9>"$LOCK_FILE"
flock -w 120 9

curl -fsS --max-time 5 http://127.0.0.1:18104/readyz >/dev/null
python3 - "$STATE_DIR/admin-auth/admin_auth.json" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text())
if not isinstance(value.get("users", {}).get("admin", {}).get("password"), dict):
    raise SystemExit("migrated admin account is unavailable")
PY

# This backup is specific to the current admin-only attempt. Do not reuse an
# older full-cutover backup because unrelated Nginx routes may have changed.
cp -- "$NGINX_CONFIG" "$BACKUP"
chmod 600 "$BACKUP"

changed=0
rollback_on_error() {
  status=$?
  if [[ "$changed" == "1" && "$status" != "0" && -f "$BACKUP" ]]; then
    cp -- "$BACKUP" "$NGINX_CONFIG" || true
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  fi
  exit "$status"
}
trap rollback_on_error EXIT

python3 "$CODE_DIR/greenfield/deploy/nginx_switch.py" \
  --config "$NGINX_CONFIG" \
  --backup "$BACKUP" \
  --admin-only
changed=1
nginx -t
systemctl reload nginx

local_status=""
for _attempt in {1..20}; do
  local_status="$(curl -sS -o /dev/null -w '%{http_code}' \
    --resolve zendesk.smlershou.top:443:127.0.0.1 --max-time 10 \
    https://zendesk.smlershou.top/admin/api/config || true)"
  [[ "$local_status" == "401" ]] && break
  sleep 0.5
done
test "$local_status" = "401"

# Nginx reload and public edge propagation can briefly serve the previous
# worker. Retry a bounded number of times before rolling back.
status=""
for _attempt in {1..20}; do
  status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
    https://zendesk.smlershou.top/admin/api/config || true)"
  [[ "$status" == "401" ]] && break
  sleep 0.5
done
test "$status" = "401"
grep -q '127.0.0.1:18104' "$NGINX_CONFIG"
grep -q 'location /webhook/' "$NGINX_CONFIG"

changed=0
trap - EXIT
echo "Authenticated Greenfield Admin is live; legacy Agent routes are unchanged."
