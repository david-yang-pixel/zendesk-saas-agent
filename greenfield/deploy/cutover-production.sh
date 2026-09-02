#!/usr/bin/env bash
# Verify one real no-write run, then permanently replace the legacy Agent.

set -euo pipefail
umask 077

CODE_DIR="${ZD_AGENT_CODE_DIR:-/opt/zd-agent/code}"
STATE_DIR="${CASEWORK_STATE_DIR:-/opt/zd-agent/greenfield}"
RUNTIME_ENV="$STATE_DIR/runtime.env"
COMPOSE_FILE="$CODE_DIR/greenfield/deploy/compose.release.yaml"
LOCK_FILE="${ZD_AGENT_DEPLOY_LOCK_FILE:-/run/lock/zd-agent-deploy.lock}"
NGINX_CONFIG="${CASEWORK_NGINX_CONFIG:-/etc/nginx/sites-enabled/zd-agent.conf}"
NGINX_BACKUP="$STATE_DIR/nginx.legacy.conf"
PROBE_TICKET_ID="${CASEWORK_PROBE_TICKET_ID:-3726}"

exec 9>"$LOCK_FILE"
flock -w 120 9
test -f "$RUNTIME_ENV"
compose=(docker compose --project-name casework-greenfield --env-file "$RUNTIME_ENV" -f "$COMPOSE_FILE")

rollback_needed=1
nginx_changed=0
rollback_on_error() {
  status=$?
  if [[ "$rollback_needed" == "1" && "$status" != "0" ]]; then
    echo "Cutover failed before legacy removal; restoring the pre-cutover route." >&2
    if [[ "$nginx_changed" == "1" && -f "$NGINX_BACKUP" ]]; then
      cp -- "$NGINX_BACKUP" "$NGINX_CONFIG" || true
      nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
    fi
    sed -i 's/^CASEWORK_DELIVERY_MODE=.*/CASEWORK_DELIVERY_MODE="dry_run"/' "$RUNTIME_ENV" || true
    python3 "$CODE_DIR/greenfield/deploy/configure_runtime.py" \
      --base-url http://127.0.0.1:18104 \
      --internal-token-file "$STATE_DIR/admin-auth/internal-token" \
      --tool-names "$STATE_DIR/tool-names.json" \
      --pause || true
    "${compose[@]}" up -d --force-recreate harness >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap rollback_on_error EXIT

if ! grep -q '^CASEWORK_DELIVERY_MODE="dry_run"$' "$RUNTIME_ENV"; then
  echo "Cutover must start from dry_run mode" >&2
  exit 1
fi
for url in \
  http://127.0.0.1:18101/readyz \
  http://127.0.0.1:18102/readyz \
  http://127.0.0.1:18104/readyz; do
  curl -fsS --max-time 5 "$url" >/dev/null
done

# Enable execution while delivery is still physically fenced by dry_run, then
# exercise the real ticket fetch, model, Tool Loop and Sandbox lifecycle.
python3 "$CODE_DIR/greenfield/deploy/configure_runtime.py" \
  --base-url http://127.0.0.1:18104 \
  --internal-token-file "$STATE_DIR/admin-auth/internal-token" \
  --tool-names "$STATE_DIR/tool-names.json" \
  --activate
python3 "$CODE_DIR/greenfield/deploy/dry_run_probe.py" \
  --ticket-id "$PROBE_TICKET_ID" --timeout-seconds 900 \
  --internal-token-file "$STATE_DIR/admin-auth/internal-token"

# Turn on the delivery adapter only after the no-write run has completed.
sed -i 's/^CASEWORK_DELIVERY_MODE=.*/CASEWORK_DELIVERY_MODE="live"/' "$RUNTIME_ENV"
"${compose[@]}" up -d --force-recreate harness
for _ in $(seq 1 60); do
  curl -fsS --max-time 3 http://127.0.0.1:18102/readyz >/dev/null && break
  sleep 3
done
curl -fsS --max-time 5 http://127.0.0.1:18102/readyz >/dev/null

# Docker health status can lag behind a successful HTTP readiness response by
# one healthcheck interval after a forced recreation. Wait for that status to
# converge before exposing the new routes so a healthy service is not treated
# as a failed cutover merely because it is still marked "starting".
for service in ingress harness admin; do
  container="casework-greenfield-${service}-1"
  healthy=0
  for _ in $(seq 1 60); do
    status="$(docker inspect "$container" --format '{{.State.Health.Status}}')"
    if [[ "$status" == "healthy" ]]; then
      healthy=1
      break
    fi
    if [[ "$status" == "unhealthy" ]]; then
      docker logs --tail 100 "$container" >&2 || true
      break
    fi
    sleep 2
  done
  test "$healthy" = "1"
done

# Render, validate and expose only the Greenfield routes. The legacy process
# remains available only until the public Greenfield checks have passed.
python3 "$CODE_DIR/greenfield/deploy/nginx_switch.py" \
  --config "$NGINX_CONFIG" --backup "$NGINX_BACKUP"
nginx_changed=1
nginx -t
systemctl reload nginx

curl -fsS --max-time 10 https://zendesk.smlershou.top/healthz >/dev/null
curl -fsS --max-time 10 https://zendesk.smlershou.top/admin/ >/dev/null

test -z "$(docker ps --filter 'label=casework.managed=true' --format '{{.Names}}')"

# The new routes and services are healthy. Permanently remove the legacy
# consumer so it cannot be restarted accidentally or process duplicate work.
systemctl disable --now zd-agent.service >/dev/null
docker rm -f zd-agent >/dev/null 2>&1 || true
rm -f /etc/systemd/system/zd-agent.service
systemctl daemon-reload
systemctl reset-failed zd-agent.service >/dev/null 2>&1 || true
test ! -e /etc/systemd/system/zd-agent.service
test -z "$(docker ps -a --filter 'name=^/zd-agent$' --format '{{.Names}}')"
! grep -q '127\.0\.0\.1:8080' "$NGINX_CONFIG"
rm -f "$NGINX_BACKUP"

rollback_needed=0
trap - EXIT
echo "Production now uses only the Greenfield stack; legacy routes and service were removed."
