#!/usr/bin/env bash
set -euo pipefail

# Permanently fence this host before copying its final Agent state to a new host.
# Usage on the OLD host: ./deploy/demote-instance.sh <new-host-instance-id>

umask 077
NEW_INSTANCE_ID="${1:-}"
SERVICE="${ZD_AGENT_SERVICE:-zd-agent}"
RESOURCES_DIR="${ZD_AGENT_RESOURCES_DIR:-/opt/zd-agent/resources}"
STATE_DIR="${RESOURCES_DIR}/.state"
MARKER="${ZD_AGENT_INSTANCE_FENCE_FILE:-${STATE_DIR}/active_instance_id}"
LOCK_FILE="${ZD_AGENT_DEPLOY_LOCK_FILE:-/run/lock/zd-agent-deploy.lock}"
LOCAL_INSTANCE_ID="${ZD_AGENT_INSTANCE_ID:-}"

if [[ ! "$NEW_INSTANCE_ID" =~ ^[A-Za-z0-9._:-]{3,128}$ ]]; then
  echo "A safe new-host instance ID is required" >&2
  exit 2
fi
if [[ "$RESOURCES_DIR" != /* || "$STATE_DIR" == "/" || \
  "$STATE_DIR" != "$RESOURCES_DIR/"* || ! -d "$STATE_DIR" || \
  -L "$STATE_DIR" ]]; then
  echo "Agent state directory is missing or unsafe" >&2
  exit 2
fi
state_real="$(cd -- "$STATE_DIR" && pwd -P)"
marker_parent_real="$(cd -- "$(dirname -- "$MARKER")" && pwd -P)"
if [[ "$marker_parent_real" != "$state_real" || ! -f "$MARKER" || \
  -L "$MARKER" ]]; then
  echo "Active instance marker is missing or outside the Agent state directory" >&2
  exit 2
fi
if [[ -z "$LOCAL_INSTANCE_ID" ]]; then
  LOCAL_INSTANCE_ID="$(tr -d '[:space:]' </etc/machine-id)"
fi
ACTIVE_INSTANCE_ID="$(tr -d '[:space:]' <"$MARKER")"
if [[ ! "$LOCAL_INSTANCE_ID" =~ ^[A-Za-z0-9._:-]{3,128}$ || \
  "$ACTIVE_INSTANCE_ID" != "$LOCAL_INSTANCE_ID" ]]; then
  echo "This host does not own the active Agent instance marker" >&2
  exit 1
fi
if [[ "$NEW_INSTANCE_ID" == "$LOCAL_INSTANCE_ID" ]]; then
  echo "The new host identity must differ from the current host" >&2
  exit 2
fi

exec 9>"$LOCK_FILE"
if ! flock -w 120 9; then
  echo "Timed out waiting for the Agent deployment lock" >&2
  exit 1
fi

echo "Stopping and permanently masking ${SERVICE} on the old host"
systemctl stop "$SERVICE"
systemctl disable "$SERVICE" >/dev/null 2>&1 || true
systemctl mask --force "$SERVICE"
active_state="$(
  systemctl show "$SERVICE" --property=ActiveState --value 2>/dev/null || true
)"
enabled_state="$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
running_container="$(
  docker ps --filter "name=^/${SERVICE}$" --format '{{.ID}}' 2>/dev/null || true
)"
if [[ "$active_state" == "active" || "$active_state" == "activating" || \
  "$active_state" == "reloading" || "$enabled_state" != "masked" || \
  -n "$running_container" ]]; then
  echo "Old Agent instance could not be fenced; marker was not transferred" >&2
  exit 1
fi

marker_temp="$(mktemp "${STATE_DIR}/.active-instance.XXXXXX")"
trap 'rm -f "$marker_temp"' EXIT
printf '%s\n' "$NEW_INSTANCE_ID" >"$marker_temp"
chmod 0600 "$marker_temp"
mv -- "$marker_temp" "$MARKER"
trap - EXIT

echo "Old host fenced. Final-sync ${STATE_DIR}/ to the new host before starting it."
echo "Transferred active instance identity: ${NEW_INSTANCE_ID}"
