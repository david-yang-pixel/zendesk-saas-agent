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
FRESHNESS_ROUTE_URL="${ZD_AGENT_FRESHNESS_ROUTE_URL:-http://127.0.0.1:8080/webhook/relay-pre-send-freshness}"
IMAGE_KEEP_RECENT="${ZD_AGENT_IMAGE_KEEP_RECENT:-3}"

REMOTE_IMAGE="${IMAGE}:${TAG}"

if [[ ! "$IMAGE_KEEP_RECENT" =~ ^[1-9][0-9]*$ ]]; then
  echo "ZD_AGENT_IMAGE_KEEP_RECENT must be a positive integer" >&2
  exit 2
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

prune_historical_agent_images() {
  local running_id local_id
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

echo "Tagging ${REMOTE_IMAGE} as ${LOCAL_IMAGE}"
docker tag "$REMOTE_IMAGE" "$LOCAL_IMAGE"

echo "Restarting ${SERVICE}"
systemctl restart "$SERVICE"

echo "Waiting for health check: ${HEALTH_URL}"
for ((attempt = 1; attempt <= HEALTH_TIMEOUT_SECONDS; attempt++)); do
  if curl -fsS "$HEALTH_URL"; then
    freshness_status="$({ curl -sS -o /dev/null -w '%{http_code}' \
      -X POST -H 'Content-Type: application/json' --data '{}' \
      "$FRESHNESS_ROUTE_URL" || true; } 2>/dev/null)"
    if [[ "$freshness_status" != "401" ]]; then
      echo "Freshness callback route check failed: expected 401, got ${freshness_status:-none}" >&2
      sleep 1
      continue
    fi
    prune_historical_agent_images
    echo
    echo "Deployment complete: ${REMOTE_IMAGE} -> ${LOCAL_IMAGE}"
    exit 0
  fi
  sleep 1
done

echo "Health check failed after ${HEALTH_TIMEOUT_SECONDS}s" >&2
systemctl status "$SERVICE" --no-pager >&2 || true
exit 1
