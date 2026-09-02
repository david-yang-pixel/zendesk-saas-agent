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
SOURCE_ENV="${ZD_AGENT_SOURCE_ENV:-/opt/zd-agent/.env}"
COMPOSE_FILE="$CODE_DIR/greenfield/deploy/compose.release.yaml"
RUNTIME_ENV="$STATE_DIR/runtime.env"
RELEASE_ENV="$STATE_DIR/release.env"
PREVIOUS_RELEASE_ENV="$STATE_DIR/previous-release.env"
LOCK_FILE="${ZD_AGENT_DEPLOY_LOCK_FILE:-/run/lock/zd-agent-deploy.lock}"

required=(
  GHCR_USERNAME GHCR_PAT CASEWORK_RELEASE_REVISION
  CASEWORK_INGRESS_IMAGE CASEWORK_HARNESS_IMAGE
  CASEWORK_SANDBOX_IMAGE CASEWORK_ADMIN_IMAGE
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
for image_name in CASEWORK_INGRESS_IMAGE CASEWORK_HARNESS_IMAGE CASEWORK_SANDBOX_IMAGE CASEWORK_ADMIN_IMAGE; do
  if [[ ! "${!image_name}" =~ @sha256:[0-9a-f]{64}$ ]]; then
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
if [[ -f "$RELEASE_ENV" ]]; then
  cp "$RELEASE_ENV" "$PREVIOUS_RELEASE_ENV"
  chmod 600 "$PREVIOUS_RELEASE_ENV"
fi

python3 "$CODE_DIR/greenfield/deploy/prepare_runtime.py" \
  --source-env "$SOURCE_ENV" \
  --state-dir "$STATE_DIR" \
  --revision "$CASEWORK_RELEASE_REVISION"

cleanup() {
  docker logout ghcr.io >/dev/null 2>&1 || true
}
trap cleanup EXIT
printf '%s' "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin >/dev/null

compose=(docker compose --project-name casework-greenfield --env-file "$RUNTIME_ENV" -f "$COMPOSE_FILE")
"${compose[@]}" config --quiet
"${compose[@]}" pull postgres ingress harness admin migration
docker pull "$CASEWORK_SANDBOX_IMAGE" >/dev/null

"${compose[@]}" up -d postgres
"${compose[@]}" run --rm migration
"${compose[@]}" up -d ingress harness admin

wait_ready() {
  local name="$1"
  local url="$2"
  local attempt
  for attempt in $(seq 1 60); do
    if curl -fsS --max-time 3 "$url" >/dev/null; then
      printf '%s ready\n' "$name"
      return 0
    fi
    sleep 3
  done
  echo "${name} did not become ready" >&2
  return 1
}

wait_ready ingress http://127.0.0.1:18101/readyz
wait_ready admin http://127.0.0.1:18104/readyz
wait_ready harness http://127.0.0.1:18102/readyz

python3 "$CODE_DIR/greenfield/deploy/migrate_legacy_model_config.py" \
  --base-url http://127.0.0.1:18104 \
  --internal-token-file "$STATE_DIR/admin-auth/internal-token" \
  --state-dir "$STATE_DIR"

python3 "$CODE_DIR/greenfield/deploy/configure_runtime.py" \
  --base-url http://127.0.0.1:18104 \
  --internal-token-file "$STATE_DIR/admin-auth/internal-token" \
  --tool-names "$STATE_DIR/tool-names.json"

cat >"$RELEASE_ENV" <<EOF
CASEWORK_RELEASE_REVISION=$CASEWORK_RELEASE_REVISION
CASEWORK_INGRESS_IMAGE=$CASEWORK_INGRESS_IMAGE
CASEWORK_HARNESS_IMAGE=$CASEWORK_HARNESS_IMAGE
CASEWORK_SANDBOX_IMAGE=$CASEWORK_SANDBOX_IMAGE
CASEWORK_ADMIN_IMAGE=$CASEWORK_ADMIN_IMAGE
EOF
chmod 600 "$RELEASE_ENV"

for service in ingress harness admin; do
  container="casework-greenfield-${service}-1"
  docker inspect "$container" --format '{{.Name}} {{.Image}} {{.State.Health.Status}}'
done
echo "Greenfield bundle is ready; the existing Agent run state was preserved."
