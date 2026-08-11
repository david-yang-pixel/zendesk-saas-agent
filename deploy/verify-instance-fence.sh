#!/usr/bin/env bash
set -euo pipefail

RESOURCES_DIR="${ZD_AGENT_RESOURCES_DIR:-/opt/zd-agent/resources}"
MARKER="${ZD_AGENT_INSTANCE_FENCE_FILE:-${RESOURCES_DIR}/.state/active_instance_id}"
INSTANCE_ID="${ZD_AGENT_INSTANCE_ID:-}"

if [[ -z "$INSTANCE_ID" ]]; then
  INSTANCE_ID="$(tr -d '[:space:]' </etc/machine-id)"
fi
if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == *$'\n'* ]]; then
  echo "Local Agent instance identity is unavailable" >&2
  exit 1
fi
if [[ ! -f "$MARKER" || -L "$MARKER" ]]; then
  echo "Active Agent instance marker is missing or unsafe: ${MARKER}" >&2
  exit 1
fi
ACTIVE_ID="$(tr -d '[:space:]' <"$MARKER")"
if [[ "$ACTIVE_ID" != "$INSTANCE_ID" ]]; then
  echo "Host is fenced from Agent side effects (active=${ACTIVE_ID:-missing}, local=${INSTANCE_ID})" >&2
  exit 1
fi
