#!/usr/bin/env bash
set -euo pipefail

# Restore a downgrade-safe release bundle retained by deploy-ghcr.sh.
# Usage: ./deploy/rollback-release.sh <40-hex previous revision>

umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REVISION="${1:-}"
SERVICE="${ZD_AGENT_SERVICE:-zd-agent}"
LOCAL_IMAGE="${ZD_AGENT_LOCAL_IMAGE:-zd-agent:latest}"
HEALTH_URL="${ZD_AGENT_HEALTH_URL:-http://127.0.0.1:8080/healthz}"
HEALTH_TIMEOUT_SECONDS="${ZD_AGENT_HEALTH_TIMEOUT_SECONDS:-45}"
CONFIG_FILE="${ZD_AGENT_CONFIG_FILE:-/opt/zd-agent/code/config.yaml}"
RESOURCES_DIR="${ZD_AGENT_RESOURCES_DIR:-/opt/zd-agent/resources}"
STATE_DIR="${RESOURCES_DIR}/.state"
ROLLBACK_DIR="${ZD_AGENT_ROLLBACK_DIR:-${RESOURCES_DIR}/.release-rollbacks}"
SYSTEMD_UNIT_TARGET="${ZD_AGENT_SYSTEMD_UNIT_TARGET:-/etc/systemd/system/zd-agent.service}"
DEPLOY_LOCK_FILE="${ZD_AGENT_DEPLOY_LOCK_FILE:-/run/lock/zd-agent-deploy.lock}"

if [[ ! "$REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Rollback requires an exact 40-hex revision" >&2
  exit 2
fi
if [[ "$RESOURCES_DIR" != /* ]] || [[ "$STATE_DIR" == "/" ]] || \
  [[ "$STATE_DIR" != "$RESOURCES_DIR/"* ]] || \
  [[ "$ROLLBACK_DIR" != "$RESOURCES_DIR/"* ]]; then
  echo "Rollback paths must be absolute and scoped under resources" >&2
  exit 2
fi
if [[ ! "$HEALTH_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ZD_AGENT_HEALTH_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
fi

exec 9>"$DEPLOY_LOCK_FILE"
if ! flock -w 120 9; then
  echo "Timed out waiting for the zd-agent deployment lock" >&2
  exit 1
fi

BUNDLE="${ROLLBACK_DIR}/${REVISION}"
MANIFEST="${BUNDLE}/manifest"
for required in "$MANIFEST" "$BUNDLE/state.tar" "$BUNDLE/config.yaml" \
  "$BUNDLE/zd-agent.service"; do
  if [[ ! -f "$required" || -L "$required" ]]; then
    echo "Rollback bundle is incomplete or unsafe: ${required}" >&2
    exit 1
  fi
done

manifest_revision="$(awk -F= '$1 == "previous_revision" { print $2 }' "$MANIFEST")"
previous_image_id="$(awk -F= '$1 == "previous_image_id" { print $2 }' "$MANIFEST")"
expected_state_sha256="$(awk -F= '$1 == "state_sha256" { print $2 }' "$MANIFEST")"
expected_config_sha256="$(awk -F= '$1 == "config_sha256" { print $2 }' "$MANIFEST")"
expected_unit_sha256="$(awk -F= '$1 == "unit_sha256" { print $2 }' "$MANIFEST")"
if [[ "$manifest_revision" != "$REVISION" ]] || \
  [[ ! "$previous_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Rollback manifest identity is invalid" >&2
  exit 1
fi
if ! tar -tf "$BUNDLE/state.tar" >/dev/null; then
  echo "Rollback state archive is invalid" >&2
  exit 1
fi
actual_state_sha256="$(sha256sum "$BUNDLE/state.tar")"
actual_config_sha256="$(sha256sum "$BUNDLE/config.yaml")"
actual_unit_sha256="$(sha256sum "$BUNDLE/zd-agent.service")"
if [[ "${actual_state_sha256%% *}" != "$expected_state_sha256" ]] || \
  [[ "${actual_config_sha256%% *}" != "$expected_config_sha256" ]] || \
  [[ "${actual_unit_sha256%% *}" != "$expected_unit_sha256" ]]; then
  echo "Rollback bundle checksum validation failed" >&2
  exit 1
fi
previous_image_revision="$(
  docker image inspect "$previous_image_id" \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
)"
if [[ "$previous_image_revision" != "$REVISION" ]]; then
  echo "Retained image revision does not match the rollback bundle" >&2
  exit 1
fi

current_revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if ! current_image_id="$(docker inspect "$SERVICE" --format '{{.Image}}')"; then
  current_image_id="$(
    docker image inspect "$LOCAL_IMAGE" --format '{{.Id}}'
  )"
fi
current_config_backup="$(mktemp "${CONFIG_FILE}.forward-backup.XXXXXX")"
current_unit_backup="$(mktemp "${SYSTEMD_UNIT_TARGET}.forward-backup.XXXXXX")"
cp -p -- "$CONFIG_FILE" "$current_config_backup"
cp -p -- "$SYSTEMD_UNIT_TARGET" "$current_unit_backup"
failed_target_state_dir=""
rollback_armed=1

stop_and_fence() {
  local active_state running_container
  systemctl stop "$SERVICE"
  active_state="$(
    systemctl show "$SERVICE" --property=ActiveState --value 2>/dev/null || true
  )"
  if [[ "$active_state" == "active" || "$active_state" == "activating" || \
    "$active_state" == "reloading" ]]; then
    return 1
  fi
  running_container="$(
    docker ps --filter "name=^/${SERVICE}$" --format '{{.ID}}' 2>/dev/null || true
  )"
  [[ -z "$running_container" ]]
}

wait_for_health() {
  local attempt
  for ((attempt = 1; attempt <= HEALTH_TIMEOUT_SECONDS; attempt++)); do
    if systemctl is-active --quiet "$SERVICE" && curl -fsS "$HEALTH_URL"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

restore_forward_release() {
  local failed=0
  rollback_armed=0
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  if [[ -n "$failed_target_state_dir" && -d "$failed_target_state_dir" ]]; then
    rm -rf "$STATE_DIR"
    mv -- "$failed_target_state_dir" "$STATE_DIR" || failed=1
    failed_target_state_dir=""
  fi
  git -C "$REPO_ROOT" checkout --detach "$current_revision" || failed=1
  install -m 0644 "$current_config_backup" "$CONFIG_FILE" || failed=1
  install -m 0644 "$current_unit_backup" "$SYSTEMD_UNIT_TARGET" || failed=1
  docker tag "$current_image_id" "$LOCAL_IMAGE" || failed=1
  systemctl daemon-reload || failed=1
  if (( failed == 0 )); then
    systemctl restart "$SERVICE" || failed=1
  fi
  if (( failed == 0 )) && ! wait_for_health; then
    failed=1
  fi
  if (( failed == 0 )); then
    restored_forward_image="$(
      docker inspect "$SERVICE" --format '{{.Image}}' 2>/dev/null || true
    )"
    if [[ "$restored_forward_image" != "$current_image_id" ]]; then
      failed=1
    fi
  fi
  if (( failed > 0 )); then
    echo "Forward release recovery was incomplete; manual recovery required" >&2
  else
    echo "Failed rollback was reversed to ${current_revision}" >&2
  fi
  return "$failed"
}

handle_exit() {
  local status="$?"
  trap '' HUP INT TERM
  trap - EXIT ERR
  if (( rollback_armed > 0 )); then
    restore_forward_release || status=1
  fi
  rm -f "$current_config_backup" "$current_unit_backup"
  exit "$status"
}
trap handle_exit EXIT

handle_signal() {
  exit "$((128 + $1))"
}
trap 'handle_signal 1' HUP
trap 'handle_signal 2' INT
trap 'handle_signal 15' TERM

echo "Stopping and fencing ${SERVICE} before manual rollback"
stop_and_fence
failed_target_state_dir="$(
  mktemp -d "${RESOURCES_DIR}/.state.forward-release.XXXXXX"
)"
rmdir "$failed_target_state_dir"
mv -- "$STATE_DIR" "$failed_target_state_dir"
mkdir -m 0700 "$STATE_DIR"
tar -C "$STATE_DIR" -xpf "$BUNDLE/state.tar"

git -C "$REPO_ROOT" checkout --detach "$REVISION"
install -m 0644 "$BUNDLE/config.yaml" "$CONFIG_FILE"
install -m 0644 "$BUNDLE/zd-agent.service" "$SYSTEMD_UNIT_TARGET"
docker tag "$previous_image_id" "$LOCAL_IMAGE"
systemctl daemon-reload
systemctl restart "$SERVICE"
wait_for_health
running_image_id="$(docker inspect "$SERVICE" --format '{{.Image}}')"
if [[ "$running_image_id" != "$previous_image_id" ]]; then
  echo "Rollback service is not running the retained image" >&2
  exit 1
fi

rollback_armed=0
rm -rf "$failed_target_state_dir"
failed_target_state_dir=""
rm -f "$current_config_backup" "$current_unit_backup"
echo "Rollback complete: ${REVISION}"
