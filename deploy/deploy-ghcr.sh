#!/usr/bin/env bash
set -euo pipefail

# Deploy a GHCR-built zd-agent image on the server.
#
# For private GHCR packages, log in once before running this script, or pass
# GHCR_USERNAME and GHCR_PAT in the environment. The PAT only needs read:packages.
# Example:
#   read -rsp "GHCR PAT: " GHCR_PAT; echo
#   ZD_AGENT_IMAGE_DIGEST=sha256:<digest> \
#     GHCR_USERNAME=<github-user> GHCR_PAT="$GHCR_PAT" \
#     ./deploy/deploy-ghcr.sh sha-<40-hex-commit>

umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
IMAGE="${ZD_AGENT_IMAGE:-ghcr.io/david-yang-pixel/zendesk-saas-agent}"
TAG="${1:-${ZD_AGENT_IMAGE_TAG:-main}}"
IMAGE_DIGEST="${ZD_AGENT_IMAGE_DIGEST:-}"
LOCAL_IMAGE="${ZD_AGENT_LOCAL_IMAGE:-zd-agent:latest}"
SERVICE="${ZD_AGENT_SERVICE:-zd-agent}"
HEALTH_URL="${ZD_AGENT_HEALTH_URL:-http://127.0.0.1:8080/healthz}"
HEALTH_TIMEOUT_SECONDS="${ZD_AGENT_HEALTH_TIMEOUT_SECONDS:-45}"
FRESHNESS_ROUTE_URL="${ZD_AGENT_FRESHNESS_ROUTE_URL:-https://zendesk.smlershou.top/webhook/relay-pre-send-freshness}"
IMAGE_KEEP_RECENT="${ZD_AGENT_IMAGE_KEEP_RECENT:-3}"
ENV_FILE="${ZD_AGENT_ENV_FILE:-/opt/zd-agent/.env}"
CONFIG_FILE="${ZD_AGENT_CONFIG_FILE:-/opt/zd-agent/code/config.yaml}"
RESOURCES_DIR="${ZD_AGENT_RESOURCES_DIR:-/opt/zd-agent/resources}"
STATE_DIR="${RESOURCES_DIR}/.state"
ROLLBACK_DIR="${ZD_AGENT_ROLLBACK_DIR:-${RESOURCES_DIR}/.release-rollbacks}"
ROLLBACK_KEEP_RECENT="${ZD_AGENT_ROLLBACK_KEEP_RECENT:-2}"
SYSTEMD_UNIT_SOURCE="${ZD_AGENT_SYSTEMD_UNIT_SOURCE:-${SCRIPT_DIR}/systemd/zd-agent.service}"
SYSTEMD_UNIT_TARGET="${ZD_AGENT_SYSTEMD_UNIT_TARGET:-/etc/systemd/system/zd-agent.service}"
DEPLOY_LOCK_FILE="${ZD_AGENT_DEPLOY_LOCK_FILE:-/run/lock/zd-agent-deploy.lock}"
INSTANCE_FENCE_SCRIPT="${ZD_AGENT_INSTANCE_FENCE_SCRIPT:-${SCRIPT_DIR}/verify-instance-fence.sh}"

if [[ "$RESOURCES_DIR" != /* ]] || [[ "$STATE_DIR" == "/" ]] || \
  [[ "$STATE_DIR" != "$RESOURCES_DIR/"* ]] || \
  [[ "$ROLLBACK_DIR" != "$RESOURCES_DIR/"* ]]; then
  echo "ZD_AGENT_RESOURCES_DIR must be an absolute, scoped directory" >&2
  exit 2
fi

if [[ ! "$TAG" =~ ^sha-([0-9a-f]{40})$ ]]; then
  echo "Deployment requires an immutable sha-<40 hex commit> tag" >&2
  exit 2
fi
EXPECTED_REVISION="${BASH_REMATCH[1]}"
if [[ ! "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ZD_AGENT_IMAGE_DIGEST must be an immutable sha256 digest" >&2
  exit 2
fi
REMOTE_IMAGE="${IMAGE}@${IMAGE_DIGEST}"
REVISION_IMAGE="${IMAGE}:${TAG}"

if [[ ! "$IMAGE_KEEP_RECENT" =~ ^[1-9][0-9]*$ ]]; then
  echo "ZD_AGENT_IMAGE_KEEP_RECENT must be a positive integer" >&2
  exit 2
fi
if [[ ! "$HEALTH_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ZD_AGENT_HEALTH_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
fi
if [[ ! "$ROLLBACK_KEEP_RECENT" =~ ^[1-9][0-9]*$ ]]; then
  echo "ZD_AGENT_ROLLBACK_KEEP_RECENT must be a positive integer" >&2
  exit 2
fi
if ! ZD_AGENT_RESOURCES_DIR="$RESOURCES_DIR" "$INSTANCE_FENCE_SCRIPT"; then
  echo "Refusing deployment on a host that does not own the Agent instance fence" >&2
  exit 1
fi

contains_image_id() {
  local expected="$1"
  shift
  local candidate
  for candidate in "$@"; do
    if [[ "$candidate" == "$expected" ]]; then
      return 0
    fi
  done
  return 1
}

copy_yaml_section() {
  local section="$1" source="$2" target="$3"
  [[ -f "$source" ]] || return 0
  if ! awk -v header="${section}:" '
    $0 == header { copying = 1; print; next }
    copying && /^[^[:space:]#][^:]*:/ { exit }
    copying { print }
  ' "$source" >"$target"; then
    rm -f "$target"
    return 1
  fi
  [[ -s "$target" ]] || rm -f "$target"
}

validate_target_image_config() {
  local image_ref="$1"
  preflight_container_name="zd-agent-preflight-$$-${RANDOM}"
  if ! preflight_env_file="$(mktemp)"; then
    preflight_env_file=""
    return 1
  fi
  if ! preflight_resources_dir="$(mktemp -d)"; then
    rm -f "$preflight_env_file"
    preflight_env_file=""
    preflight_resources_dir=""
    return 1
  fi
  if ! mkdir -m 0700 "${preflight_resources_dir}/.state" || \
    ! copy_yaml_section \
    public_reply \
    "${RESOURCES_DIR}/.state/admin_overrides.yaml" \
    "${preflight_resources_dir}/.state/admin_overrides.yaml" || \
    ! copy_yaml_section \
    public_reply \
    "${RESOURCES_DIR}/.state/admin_secrets.yaml" \
    "${preflight_resources_dir}/.state/admin_secrets.yaml"; then
    rm -rf "$preflight_env_file" "$preflight_resources_dir"
    preflight_env_file=""
    preflight_resources_dir=""
    return 1
  fi
  if ! awk -F= '
    $1 == "ZENDESK_BROWSER_RELAY_URL" ||
    $1 == "ZENDESK_BROWSER_RELAY_API_KEY" ||
    $1 == "ZENDESK_FRESHNESS_CALLBACK_URL" { print }
  ' "$ENV_FILE" >"$preflight_env_file"; then
    rm -rf "$preflight_env_file" "$preflight_resources_dir"
    preflight_env_file=""
    preflight_resources_dir=""
    return 1
  fi
  if timeout -k 5s 30s docker run --rm \
    --name "$preflight_container_name" \
    --network none --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 64 \
    --memory=128m --memory-swap=128m --cpus=0.25 \
    --log-driver none \
    --env-file "$preflight_env_file" \
    -v "${CONFIG_FILE}:/app/config.yaml:ro" \
    -v "${preflight_resources_dir}:/opt/zd-agent/resources:ro" \
    --entrypoint /app/.venv/bin/python \
    "$image_ref" -c \
    'from pathlib import Path; from zd_agent.config import get_config; from zd_agent.services.admin_config import AdminConfigStore; from zd_agent.tools.public_reply_delivery import validate_public_reply_transport_config; cfg = get_config(); state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir; AdminConfigStore(state_dir).apply_saved(cfg); validate_public_reply_transport_config(cfg)' \
    >/dev/null 2>&1; then
    cleanup_temporary_files
    return 0
  fi
  cleanup_temporary_files
  return 1
}

validate_running_config() {
  docker exec "$SERVICE" /app/.venv/bin/python -c \
    'from zd_agent.config import get_config; from zd_agent.services.app_bootstrap import apply_saved_admin_config; from zd_agent.tools.public_reply_delivery import validate_public_reply_transport_config; cfg = get_config(); apply_saved_admin_config(cfg); validate_public_reply_transport_config(cfg)'
}

rollback_armed=0
previous_image_id=""
previous_revision=""
unit_backup=""
preserve_unit_backup=0
previous_config_backup=""
state_backup=""
state_snapshot_temp=""
failed_target_state_dir=""
preserve_release_backup=0
rollback_bundle_temp=""
preflight_env_file=""
preflight_resources_dir=""
preflight_container_name=""

cleanup_temporary_files() {
  if [[ -n "$preflight_container_name" ]]; then
    docker rm -f "$preflight_container_name" >/dev/null 2>&1 || true
    preflight_container_name=""
  fi
  if [[ -n "$preflight_env_file" ]]; then
    rm -f "$preflight_env_file"
    preflight_env_file=""
  fi
  if [[ -n "$preflight_resources_dir" ]]; then
    rm -rf "$preflight_resources_dir"
    preflight_resources_dir=""
  fi
  if (( preserve_release_backup == 0 )); then
    if [[ -n "$previous_config_backup" ]]; then
      rm -f "$previous_config_backup"
      previous_config_backup=""
    fi
    if [[ -n "$state_backup" ]]; then
      rm -f "$state_backup"
      state_backup=""
    fi
    if [[ -n "$state_snapshot_temp" ]]; then
      rm -f "$state_snapshot_temp"
      state_snapshot_temp=""
    fi
    if [[ -n "$failed_target_state_dir" ]]; then
      rm -rf "$failed_target_state_dir"
      failed_target_state_dir=""
    fi
    if [[ -n "$rollback_bundle_temp" ]]; then
      rm -rf "$rollback_bundle_temp"
      rollback_bundle_temp=""
    fi
  fi
}

stop_and_fence_service() {
  local active_state running_container
  if ! systemctl stop "$SERVICE"; then
    echo "Could not stop ${SERVICE} before the state boundary" >&2
    return 1
  fi
  active_state="$(
    systemctl show "$SERVICE" --property=ActiveState --value 2>/dev/null || true
  )"
  if [[ "$active_state" == "active" || "$active_state" == "activating" || \
    "$active_state" == "reloading" ]]; then
    echo "${SERVICE} remained active after stop" >&2
    return 1
  fi
  running_container="$(
    docker ps --filter "name=^/${SERVICE}$" --format '{{.ID}}' 2>/dev/null || true
  )"
  if [[ -n "$running_container" ]]; then
    echo "${SERVICE} container remained active after stop" >&2
    return 1
  fi
  return 0
}

snapshot_state_for_rollback() {
  if [[ ! -d "$STATE_DIR" || -L "$STATE_DIR" ]]; then
    echo "State directory is missing or unsafe: ${STATE_DIR}" >&2
    return 1
  fi
  state_snapshot_temp="$(
    mktemp "${RESOURCES_DIR}/.state.deploy-backup.XXXXXX.tar"
  )"
  if ! tar -C "$STATE_DIR" -cpf "$state_snapshot_temp" . || \
    ! tar -tf "$state_snapshot_temp" >/dev/null; then
    echo "Could not snapshot ${STATE_DIR}" >&2
    rm -f "$state_snapshot_temp"
    state_snapshot_temp=""
    return 1
  fi
  state_backup="$state_snapshot_temp"
  state_snapshot_temp=""
}

restore_state_snapshot() {
  [[ -n "$state_backup" && -f "$state_backup" ]] || return 0
  if [[ ! -d "$STATE_DIR" || -L "$STATE_DIR" ]]; then
    echo "Current state directory is missing or unsafe: ${STATE_DIR}" >&2
    return 1
  fi
  failed_target_state_dir="$(
    mktemp -d "${RESOURCES_DIR}/.state.failed-target.XXXXXX"
  )"
  rmdir "$failed_target_state_dir"
  if ! mv -- "$STATE_DIR" "$failed_target_state_dir"; then
    echo "Could not preserve failed target state" >&2
    return 1
  fi
  if ! mkdir -m 0700 "$STATE_DIR" || \
    ! tar -C "$STATE_DIR" -xpf "$state_backup"; then
    rm -rf "$STATE_DIR"
    if ! mv -- "$failed_target_state_dir" "$STATE_DIR"; then
      echo "State restore failed and target state could not be recovered" >&2
    fi
    failed_target_state_dir=""
    return 1
  fi
  return 0
}

retain_rollback_bundle() {
  local bundle_dir manifest current_count old_bundle
  local state_sha256 config_sha256 unit_sha256 created_at_epoch
  [[ -n "$state_backup" && -f "$state_backup" ]] || return 1
  [[ -n "$previous_config_backup" && -f "$previous_config_backup" ]] || return 1
  [[ -n "$unit_backup" && -f "$unit_backup" ]] || return 1

  if ! mkdir -p -m 0700 "$ROLLBACK_DIR" || ! chmod 0700 "$ROLLBACK_DIR"; then
    return 1
  fi
  if ! rollback_bundle_temp="$(
    mktemp -d "${ROLLBACK_DIR}/.${previous_revision}.XXXXXX"
  )"; then
    rollback_bundle_temp=""
    return 1
  fi
  if ! install -m 0600 "$state_backup" "$rollback_bundle_temp/state.tar" || \
    ! install -m 0600 "$previous_config_backup" \
      "$rollback_bundle_temp/config.yaml" || \
    ! install -m 0600 "$unit_backup" \
      "$rollback_bundle_temp/zd-agent.service"; then
    return 1
  fi
  if ! tar -tf "$rollback_bundle_temp/state.tar" >/dev/null; then
    return 1
  fi
  if ! state_sha256="$(sha256sum "$rollback_bundle_temp/state.tar")" || \
    ! config_sha256="$(sha256sum "$rollback_bundle_temp/config.yaml")" || \
    ! unit_sha256="$(sha256sum "$rollback_bundle_temp/zd-agent.service")"; then
    return 1
  fi
  state_sha256="${state_sha256%% *}"
  config_sha256="${config_sha256%% *}"
  unit_sha256="${unit_sha256%% *}"
  if ! created_at_epoch="$(date +%s)"; then
    return 1
  fi
  manifest="$rollback_bundle_temp/manifest"
  if ! {
    printf 'previous_revision=%s\n' "$previous_revision"
    printf 'previous_image_id=%s\n' "$previous_image_id"
    printf 'created_at_epoch=%s\n' "$created_at_epoch"
    printf 'state_sha256=%s\n' "$state_sha256"
    printf 'config_sha256=%s\n' "$config_sha256"
    printf 'unit_sha256=%s\n' "$unit_sha256"
  } >"$manifest" || ! chmod 0600 "$manifest"; then
    return 1
  fi

  bundle_dir="${ROLLBACK_DIR}/${previous_revision}"
  if ! rm -rf -- "$bundle_dir" || \
    ! mv -- "$rollback_bundle_temp" "$bundle_dir"; then
    return 1
  fi
  rollback_bundle_temp=""

  if [[ ! -f "$bundle_dir/state.tar" || \
    ! -f "$bundle_dir/config.yaml" || \
    ! -f "$bundle_dir/zd-agent.service" || \
    ! -f "$bundle_dir/manifest" ]] || \
    ! tar -tf "$bundle_dir/state.tar" >/dev/null; then
    return 1
  fi

  if ! rm -f "$state_backup" "$previous_config_backup" "$unit_backup"; then
    return 1
  fi
  state_backup=""
  previous_config_backup=""
  unit_backup=""

  current_count=0
  while IFS= read -r old_bundle; do
    [[ -n "$old_bundle" ]] || continue
    current_count=$((current_count + 1))
    if (( current_count > ROLLBACK_KEEP_RECENT )); then
      rm -rf -- "$old_bundle"
    fi
  done < <(
    find "$ROLLBACK_DIR" -mindepth 1 -maxdepth 1 -type d \
      ! -name '.*' -print0 \
      | xargs -0 ls -1dt 2>/dev/null || true
  )
  echo "Retained downgrade-safe rollback bundle: ${bundle_dir}"
}

wait_for_service_health() {
  local attempt
  for ((attempt = 1; attempt <= HEALTH_TIMEOUT_SECONDS; attempt++)); do
    if systemctl is-active --quiet "$SERVICE" && curl -fsS "$HEALTH_URL"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

rollback_deployment() {
  rollback_armed=0
  local failed=0 unit_restored=0 restored_image_id="" service_stopped=0
  echo "Rolling back ${SERVICE} to the previous image, config, and state" >&2
  if stop_and_fence_service; then
    service_stopped=1
  else
    echo "Rollback could not fence the failed target service" >&2
    failed=1
  fi
  if (( service_stopped > 0 )) && ! restore_state_snapshot; then
    echo "Rollback could not restore the pre-deployment state snapshot" >&2
    failed=1
  fi
  if (( service_stopped > 0 )) && \
    ! git -C "$REPO_ROOT" checkout --detach "$previous_revision"; then
    echo "Rollback could not restore source revision ${previous_revision}" >&2
    failed=1
  fi
  if (( service_stopped > 0 )) && \
    ! install -m 0644 "$previous_config_backup" "$CONFIG_FILE"; then
    echo "Rollback could not restore revision ${previous_revision} config" >&2
    failed=1
  fi
  if ! docker tag "$previous_image_id" "$LOCAL_IMAGE"; then
    echo "Rollback could not restore ${LOCAL_IMAGE}" >&2
    failed=1
  fi
  if ! install -m 0644 "$unit_backup" "$SYSTEMD_UNIT_TARGET"; then
    echo "Rollback could not restore ${SYSTEMD_UNIT_TARGET}" >&2
    failed=1
  else
    unit_restored=1
  fi
  if ! systemctl daemon-reload; then
    echo "Rollback systemd daemon-reload failed" >&2
    failed=1
  fi
  if (( service_stopped == 0 || failed > 0 )); then
    echo "Rollback did not restart because state/config restoration was incomplete" >&2
  elif ! systemctl restart "$SERVICE"; then
    echo "Rollback could not restart ${SERVICE}" >&2
    failed=1
  fi
  if (( failed == 0 )) && ! wait_for_service_health; then
    echo "Rollback service readiness check failed" >&2
    failed=1
  fi
  if (( failed == 0 )); then
    restored_image_id="$(docker inspect "$SERVICE" --format '{{.Image}}')" || true
    if [[ "$restored_image_id" != "$previous_image_id" ]]; then
      echo "Rollback service is not running the previous image" >&2
      failed=1
    fi
  fi
  if (( failed == 0 && unit_restored > 0 )); then
    rm -f "$unit_backup"
    unit_backup=""
    rm -f "$previous_config_backup" "$state_backup"
    previous_config_backup=""
    state_backup=""
    rm -rf "$failed_target_state_dir"
    failed_target_state_dir=""
  else
    preserve_unit_backup=1
    preserve_release_backup=1
    echo "Rollback backup preserved at ${unit_backup}" >&2
    [[ -n "$state_backup" ]] && \
      echo "State snapshot preserved at ${state_backup}" >&2
    [[ -n "$previous_config_backup" ]] && \
      echo "Config snapshot preserved at ${previous_config_backup}" >&2
  fi
  if (( failed > 0 )); then
    echo "Automatic rollback was incomplete; manual recovery is required" >&2
    return 1
  fi
  echo "Rollback complete" >&2
  return 0
}

rollback_and_exit() {
  local message="$1"
  echo "$message" >&2
  exit 1
}

handle_exit() {
  local status="$?"
  trap - EXIT ERR
  trap '' HUP INT TERM
  if (( rollback_armed > 0 )); then
    if ! rollback_deployment; then
      status=1
    elif (( status == 0 )); then
      status=1
    fi
  fi
  cleanup_temporary_files
  if [[ -n "$unit_backup" && "$preserve_unit_backup" -eq 0 ]]; then
    rm -f "$unit_backup"
    unit_backup=""
  fi
  exit "$status"
}

handle_signal() {
  exit "$((128 + $1))"
}

trap handle_exit EXIT
trap 'handle_signal 1' HUP
trap 'handle_signal 2' INT
trap 'handle_signal 15' TERM

if [[ "${ZD_AGENT_DEPLOY_LOCK_HELD:-0}" == "1" ]]; then
  inherited_lock_path="$(readlink /proc/$$/fd/9 2>/dev/null || true)"
  if [[ "$inherited_lock_path" != "$DEPLOY_LOCK_FILE" ]] || ! flock -n 9; then
    echo "Inherited zd-agent deployment lock is invalid" >&2
    exit 1
  fi
else
  exec 9>"$DEPLOY_LOCK_FILE"
  if ! flock -w 120 9; then
    echo "Timed out waiting for the zd-agent deployment lock" >&2
    exit 1
  fi
fi

actual_source_revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [[ "$actual_source_revision" != "$EXPECTED_REVISION" ]] || \
  ! git -C "$REPO_ROOT" diff --quiet -- || \
  ! git -C "$REPO_ROOT" diff --cached --quiet --; then
  echo "Refusing deployment from a dirty or mixed-revision worktree" >&2
  exit 1
fi

prune_historical_agent_images() {
  local running_id local_id bundle_manifest bundle_image_id
  if ! running_id="$(docker inspect "$SERVICE" --format '{{.Image}}')"; then
    echo "Could not inspect the running Agent image; skipping cleanup" >&2
    return 0
  fi
  if ! local_id="$(docker image inspect "$LOCAL_IMAGE" --format '{{.Id}}')"; then
    echo "Could not inspect ${LOCAL_IMAGE}; skipping cleanup" >&2
    return 0
  fi
  running_id="${running_id#sha256:}"
  local_id="${local_id#sha256:}"

  local -a protected_ids=("$running_id" "$local_id")
  # A sentinel keeps these arrays valid with `set -u` on macOS Bash 3.2.
  local -a recent_ids=("__no_image__")
  local -a image_refs=("__no_image__")
  local -a image_ids=("__no_image__")
  local recent_count=0
  local created ref raw_id image_id
  local image_list sorted_images

  if [[ -d "$ROLLBACK_DIR" ]]; then
    while IFS= read -r bundle_manifest; do
      [[ -f "$bundle_manifest" && ! -L "$bundle_manifest" ]] || continue
      bundle_image_id="$(
        awk -F= '$1 == "previous_image_id" { print $2 }' "$bundle_manifest"
      )"
      if [[ "$bundle_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        protected_ids+=("${bundle_image_id#sha256:}")
      else
        echo "Ignoring invalid rollback bundle image identity: ${bundle_manifest}" >&2
      fi
    done < <(
      find "$ROLLBACK_DIR" -mindepth 2 -maxdepth 2 -type f \
        -name manifest -print 2>/dev/null || true
    )
  fi

  if ! image_list="$(
    docker image ls --no-trunc \
      --format '{{.Repository}}:{{.Tag}} {{.ID}}' \
      "$IMAGE"
  )"; then
    echo "Could not list historical Agent images; skipping cleanup" >&2
    return 0
  fi

  if ! sorted_images="$(
    while read -r ref raw_id; do
      [[ -n "$ref" && -n "$raw_id" ]] || continue
      [[ "$ref" == "$IMAGE:"* ]] || continue
      if ! created="$(docker image inspect "$ref" --format '{{.Created}}')"; then
        echo "Could not inspect historical image creation time: ${ref}" >&2
        continue
      fi
      printf '%s\t%s\t%s\n' "$created" "$ref" "$raw_id"
    done <<< "$image_list" | LC_ALL=C sort -r
  )"; then
    echo "Could not sort historical Agent images; skipping cleanup" >&2
    return 0
  fi

  while read -r created ref raw_id; do
    [[ -n "$created" && -n "$ref" && -n "$raw_id" ]] || continue
    image_id="${raw_id#sha256:}"
    image_refs+=("$ref")
    image_ids+=("$image_id")

    if (( recent_count < IMAGE_KEEP_RECENT )) && \
      ! contains_image_id "$image_id" "${recent_ids[@]}"; then
      recent_ids+=("$image_id")
      protected_ids+=("$image_id")
      recent_count=$((recent_count + 1))
    fi
  done <<< "$sorted_images"

  local index removed=0 failed=0
  for ((index = 1; index < ${#image_refs[@]}; index++)); do
    if contains_image_id "${image_ids[index]}" "${protected_ids[@]}"; then
      continue
    fi
    if docker image rm "${image_refs[index]}"; then
      removed=$((removed + 1))
    else
      echo "Could not remove historical image: ${image_refs[index]}" >&2
      failed=$((failed + 1))
    fi
  done
  echo "Historical image cleanup complete: removed ${removed}, failed ${failed}, kept recent ${IMAGE_KEEP_RECENT}"
}

if [[ -n "${GHCR_PAT:-}" ]]; then
  : "${GHCR_USERNAME:?GHCR_USERNAME is required when GHCR_PAT is set}"
  printf '%s' "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
fi

echo "Pulling ${REMOTE_IMAGE}"
docker pull "$REMOTE_IMAGE"

actual_revision="$(
  docker image inspect "$REMOTE_IMAGE" \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
)"
if [[ "$actual_revision" != "$EXPECTED_REVISION" ]]; then
  echo "Target image revision does not match ${EXPECTED_REVISION}" >&2
  exit 1
fi
target_image_id="$(
  docker image inspect "$REMOTE_IMAGE" --format '{{.Id}}'
)"

echo "Validating effective public reply config in ${REMOTE_IMAGE}"
if ! validate_target_image_config "$REMOTE_IMAGE"; then
  echo "Target image effective public reply config check failed" >&2
  exit 1
fi

if ! previous_image_id="$(docker inspect "$SERVICE" --format '{{.Image}}')"; then
  echo "Could not inspect the current ${SERVICE} image for safe rollback" >&2
  exit 1
fi
previous_revision="$(
  docker image inspect "$previous_image_id" \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
)"
if [[ ! "$previous_revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Previous Agent image has no immutable source revision for rollback" >&2
  exit 1
fi
previous_config_backup="$(mktemp "${CONFIG_FILE}.previous.XXXXXX")"
if ! git -C "$REPO_ROOT" show \
  "${previous_revision}:config.yaml" >"$previous_config_backup"; then
  echo "Could not materialize previous revision config for rollback" >&2
  exit 1
fi
unit_backup="$(mktemp "${SYSTEMD_UNIT_TARGET}.deploy-backup.XXXXXX")"
cp -p -- "$SYSTEMD_UNIT_TARGET" "$unit_backup"
rollback_armed=1

echo "Stopping and fencing ${SERVICE} before snapshot"
stop_and_fence_service
echo "Snapshotting ${STATE_DIR} for downgrade-safe rollback"
snapshot_state_for_rollback

echo "Tagging ${REMOTE_IMAGE} as ${LOCAL_IMAGE}"
docker tag "$REMOTE_IMAGE" "$REVISION_IMAGE"
docker tag "$REMOTE_IMAGE" "$LOCAL_IMAGE"

echo "Verifying systemd unit: ${SYSTEMD_UNIT_SOURCE}"
systemd-analyze verify "$SYSTEMD_UNIT_SOURCE"

echo "Syncing systemd unit: ${SYSTEMD_UNIT_SOURCE}"
install -m 0644 "$SYSTEMD_UNIT_SOURCE" "$SYSTEMD_UNIT_TARGET"
systemctl daemon-reload

echo "Restarting ${SERVICE}"
systemctl restart "$SERVICE"

echo "Waiting for health check: ${HEALTH_URL}"
for ((attempt = 1; attempt <= HEALTH_TIMEOUT_SECONDS; attempt++)); do
  if systemctl is-active --quiet "$SERVICE" && curl -fsS "$HEALTH_URL"; then
    if ! validate_running_config; then
      rollback_and_exit "Running Agent effective public reply config check failed"
    fi
    freshness_status="$({ curl -sS -o /dev/null -w '%{http_code}' \
      -X POST -H 'Content-Type: application/json' --data '{}' \
      "$FRESHNESS_ROUTE_URL" || true; } 2>/dev/null)"
    if [[ "$freshness_status" != "401" ]]; then
      echo "Freshness callback route check failed: expected 401, got ${freshness_status:-none}" >&2
      sleep 1
      continue
    fi
    running_deployed_image_id="$(
      docker inspect "$SERVICE" --format '{{.Image}}'
    )"
    if [[ "$running_deployed_image_id" != "$target_image_id" ]]; then
      rollback_and_exit "Running Agent image does not match the target digest"
    fi
    if ! retain_rollback_bundle; then
      rollback_and_exit "Could not retain downgrade-safe rollback bundle"
    fi
    rollback_armed=0
    prune_historical_agent_images
    echo
    echo "Deployment complete: ${REMOTE_IMAGE} -> ${LOCAL_IMAGE}"
    exit 0
  fi
  sleep 1
done

echo "Health check failed after ${HEALTH_TIMEOUT_SECONDS}s" >&2
systemctl status "$SERVICE" --no-pager >&2 || true
rollback_and_exit "Deployment readiness checks failed"
