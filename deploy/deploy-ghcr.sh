#!/usr/bin/env bash
set -euo pipefail

# Deploy a GHCR-built zd-agent image on the server.
#
# For private GHCR packages, log in once before running this script, or pass
# GHCR_USERNAME and GHCR_PAT in the environment. The PAT only needs read:packages.
# Example:
#   read -rsp "GHCR PAT: " GHCR_PAT; echo
#   GHCR_USERNAME=<github-user> GHCR_PAT="$GHCR_PAT" ./deploy/deploy-ghcr.sh main

IMAGE="${ZD_AGENT_IMAGE:-ghcr.io/david-yang-pixel/zendesk-saas-agent}"
TAG="${1:-${ZD_AGENT_IMAGE_TAG:-main}}"
LOCAL_IMAGE="${ZD_AGENT_LOCAL_IMAGE:-zd-agent:latest}"
SERVICE="${ZD_AGENT_SERVICE:-zd-agent}"
HEALTH_URL="${ZD_AGENT_HEALTH_URL:-http://127.0.0.1:8080/healthz}"
HEALTH_TIMEOUT_SECONDS="${ZD_AGENT_HEALTH_TIMEOUT_SECONDS:-45}"

REMOTE_IMAGE="${IMAGE}:${TAG}"

if [[ -n "${GHCR_PAT:-}" ]]; then
  : "${GHCR_USERNAME:?GHCR_USERNAME is required when GHCR_PAT is set}"
  printf '%s' "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
fi

echo "Pulling ${REMOTE_IMAGE}"
docker pull "$REMOTE_IMAGE"

echo "Tagging ${REMOTE_IMAGE} as ${LOCAL_IMAGE}"
docker tag "$REMOTE_IMAGE" "$LOCAL_IMAGE"

echo "Restarting ${SERVICE}"
systemctl restart "$SERVICE"

echo "Waiting for health check: ${HEALTH_URL}"
for ((attempt = 1; attempt <= HEALTH_TIMEOUT_SECONDS; attempt++)); do
  if curl -fsS "$HEALTH_URL"; then
    echo
    echo "Deployment complete: ${REMOTE_IMAGE} -> ${LOCAL_IMAGE}"
    exit 0
  fi
  sleep 1
done

echo "Health check failed after ${HEALTH_TIMEOUT_SECONDS}s" >&2
systemctl status "$SERVICE" --no-pager >&2 || true
exit 1
