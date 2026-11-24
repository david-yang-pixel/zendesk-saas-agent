#!/usr/bin/env bash
# Start or upgrade the Greenfield stack beside the still-running legacy Agent.
# This script does not change Nginx and does not stop the legacy service.

set -euo pipefail
umask 077

if [[ "$EUID" -ne 0 ]]; then
  echo "Greenfield production deployment must run as root for credential ownership" >&2
  exit 1
fi

CODE_DIR="${ZD_AGENT_CODE_DIR:-/opt/zd-agent/code}"
STATE_DIR="${CASEWORK_STATE_DIR:-/opt/zd-agent/greenfield}"
DEPLOY_DIR="$CODE_DIR/deploy"
if [[ ! -d "$DEPLOY_DIR" && -d "$CODE_DIR/greenfield/deploy" ]]; then
  DEPLOY_DIR="$CODE_DIR/greenfield/deploy"
fi
COMPOSE_FILE="$DEPLOY_DIR/compose.release.yaml"
RUNTIME_ENV="$STATE_DIR/runtime.env"
RELEASE_ENV="$STATE_DIR/release.env"
PREVIOUS_RELEASE_ENV="$STATE_DIR/previous-release.env"
LOCK_FILE="${ZD_AGENT_DEPLOY_LOCK_FILE:-/run/lock/zd-agent-deploy.lock}"

required=(
  GHCR_USERNAME GHCR_PAT CASEWORK_RELEASE_REVISION
  CASEWORK_INGRESS_IMAGE CASEWORK_ADMIN_IMAGE CASEWORK_PI_AGENT_IMAGE
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required release input: ${name}" >&2
    exit 1
  fi
done

if [[ ! "$CASEWORK_RELEASE_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "CASEWORK_RELEASE_REVISION must be a full Git commit SHA" >&2
  exit 1
fi
for image_name in CASEWORK_INGRESS_IMAGE CASEWORK_ADMIN_IMAGE CASEWORK_PI_AGENT_IMAGE; do
  if [[ ! "${!image_name}" =~ ^[[:alnum:]][[:alnum:].:/_-]*@sha256:[0-9a-f]{64}$ ]]; then
    echo "${image_name} must be digest-pinned" >&2
    exit 1
  fi
done

if [[ "${CASEWORK_DEPLOY_LOCK_HELD:-0}" != "1" ]]; then
  exec 9>"$LOCK_FILE"
  if ! flock -w 120 9; then
    echo "Timed out waiting for the Agent deployment lock" >&2
    exit 1
  fi
fi

install -d -m 700 "$STATE_DIR"
# shellcheck source=deploy/production-quiesce.sh
source "$DEPLOY_DIR/production-quiesce.sh"
cleanup() {
  local status=$?
  if (( status != 0 )); then
    recover_production
  fi
  docker logout ghcr.io >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s' "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin >/dev/null
for image in "$CASEWORK_INGRESS_IMAGE" "$CASEWORK_ADMIN_IMAGE" "$CASEWORK_PI_AGENT_IMAGE"; do
  docker pull "$image"
done

quiesce_production
release_change_started=1
python3 "$DEPLOY_DIR/prepare_runtime.py" \
  --state-dir "$STATE_DIR" \
  --revision "$CASEWORK_RELEASE_REVISION"
configure_production_compose
if [[ -z "$postgres_id" ]]; then
  "${compose[@]}" up -d --wait --wait-timeout 120 postgres
  find_production_db
fi
"${compose[@]}" run --rm --no-deps migration
production_db -v legacy_queue=false < "$DEPLOY_DIR/production-queue-layout.sql"
"${compose[@]}" up -d --no-deps "${services[@]}"
wait_ready ingress http://127.0.0.1:18101/readyz
wait_ready admin http://127.0.0.1:18104/readyz

python3 "$DEPLOY_DIR/migrate_legacy_model_config.py" \
  --base-url http://127.0.0.1:18104 \
  --internal-token-file "$STATE_DIR/admin-auth/internal-token" \
  --state-dir "$STATE_DIR"

python3 "$DEPLOY_DIR/configure_runtime.py" \
  --base-url http://127.0.0.1:18104 \
  --internal-token-file "$STATE_DIR/admin-auth/internal-token" \
  --tool-names "$STATE_DIR/tool-names.json"

"${compose[@]}" up -d --no-deps pi-agent
wait_ready pi-agent http://127.0.0.1:18102/readyz

cat >"$RELEASE_ENV.tmp" <<EOF
CASEWORK_RELEASE_REVISION=$CASEWORK_RELEASE_REVISION
CASEWORK_INGRESS_IMAGE=$CASEWORK_INGRESS_IMAGE
CASEWORK_ADMIN_IMAGE=$CASEWORK_ADMIN_IMAGE
CASEWORK_PI_AGENT_IMAGE=$CASEWORK_PI_AGENT_IMAGE
CASEWORK_RELEASE_QUEUE_LAYOUT=operator_mentions
EOF
if [[ -f "$RELEASE_ENV" ]] && ! cmp -s "$RELEASE_ENV" "$RELEASE_ENV.tmp"; then
  install -m 600 "$RELEASE_ENV" "$PREVIOUS_RELEASE_ENV.tmp"
  mv -f "$PREVIOUS_RELEASE_ENV.tmp" "$PREVIOUS_RELEASE_ENV"
fi
mv -f "$RELEASE_ENV.tmp" "$RELEASE_ENV"
release_claim_fence

echo 'Greenfield bundle is ready. The existing Agent run state was preserved.'
