# Shared by the production deploy and rollback entrypoints under the deployment lock.

production_containers() {
  local service="$1"
  shift
  timeout 15 docker ps -q "$@" \
    --filter label=com.docker.compose.project=casework-greenfield \
    --filter "label=com.docker.compose.service=$service"
}

production_db() {
  timeout --kill-after=2 "${production_db_timeout:-15}" docker exec -i \
    -e 'PGOPTIONS=-c lock_timeout=8000 -c statement_timeout=10000' \
    "$postgres_id" psql -X -qAt -v ON_ERROR_STOP=1 -U casework -d casework "$@"
}

find_production_db() {
  postgres_id="$(production_containers postgres -a)"
  if [[ "$postgres_id" == *$'\n'* ]]; then
    echo 'Expected one Greenfield PostgreSQL container.' >&2
    return 1
  fi
}

wait_production_db() {
  local deadline=$((SECONDS + 120))
  while ! production_db -c 'SELECT 1' >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo 'Greenfield PostgreSQL did not become ready.' >&2
      return 1
    fi
    sleep 2
  done
}

# The fence survives host/script interruption. It only blocks claims and
# expired-lease cleanup. In-flight renewal and normal finalization still work.
set_claim_fence() {
  production_db -v "enable_fence=$1" < "$DEPLOY_DIR/production-claim-fence.sql"
}

wait_for_drain() {
  local deadline=$((SECONDS + drain_timeout))
  local active remaining
  while (( (remaining = deadline - SECONDS) > 0 )); do
    if ! active="$(production_db_timeout=$((remaining < 15 ? remaining : 15)) production_db -c "
      SELECT EXISTS (
        SELECT 1 FROM public.case_jobs
        WHERE status IN ('claimed', 'sandbox_creating', 'running', 'finalizing')
      )$(if [[ "$has_handoffs" == t ]]; then
        printf '%s' " OR EXISTS (SELECT 1 FROM public.human_handoffs WHERE status = 'processing')"
      fi)")"; then
      echo 'Could not check the active Greenfield queue within the drain deadline.' >&2
      return 1
    fi
    case "$active" in
      f) return 0 ;;
      t) ;;
      *) echo 'Could not verify that the Greenfield worker drained.' >&2; return 1 ;;
    esac
    remaining=$((deadline - SECONDS))
    if (( remaining > 0 )); then
      sleep "$((remaining < 2 ? remaining : 2))"
    fi
  done
  echo "Greenfield worker did not drain within ${drain_timeout}s. Active rows may include stale leases." >&2
  return 1
}

quiesce_production() {
  local service id ids worker_id state had_fence running_postgres
  local -a workers=() writers=()
  drain_timeout="${CASEWORK_DEPLOY_DRAIN_TIMEOUT:-300}"
  if [[ ! "$drain_timeout" =~ ^[1-9][0-9]{0,2}$ ]] || (( drain_timeout > 600 )); then
    echo 'CASEWORK_DEPLOY_DRAIN_TIMEOUT must be 1–600 seconds.' >&2
    return 1
  fi

  for service in ingress admin log-sync pi-agent; do
    ids="$(production_containers "$service")"
    if [[ -n "$ids" ]]; then
      if [[ "$service" == pi-agent ]]; then
        mapfile -t workers <<< "$ids"
      else
        while IFS= read -r id; do writers+=("$id"); done <<< "$ids"
      fi
    fi
  done
  original_containers=("${writers[@]}" "${workers[@]}")

  find_production_db
  if [[ -z "$postgres_id" ]]; then
    if [[ -e "$RELEASE_ENV" || ${#original_containers[@]} -ne 0 ]]; then
      echo 'Existing Greenfield release has no PostgreSQL container. Restore it before proceeding.' >&2
      return 1
    fi
    return 0
  fi
  running_postgres="$(production_containers postgres)"
  if [[ -z "$running_postgres" ]]; then
    timeout 30 docker start "$postgres_id" >/dev/null
  fi
  wait_production_db
  state="$(production_db -c "
    SELECT (to_regclass('public.case_jobs') IS NOT NULL)::int,
           to_regclass('public.human_handoffs') IS NOT NULL,
           to_regprocedure('public.casework_deploy_block_claim()') IS NOT NULL")"
  case "$state" in
    '0|f|f')
      if [[ -e "$RELEASE_ENV" || ${#original_containers[@]} -ne 0 ]]; then
        echo 'Existing Greenfield release has no case job table.' >&2
        return 1
      fi
      return 0 ;;
    1\|[tf]\|[tf]) ;;
    *) echo 'Could not identify the Greenfield queue schema.' >&2; return 1 ;;
  esac
  IFS='|' read -r _ has_handoffs had_fence <<< "$state"
  if [[ "$had_fence" == f ]]; then
    clear_fence_on_failure=1
  fi
  set_claim_fence true
  claim_fenced=1

  # Ingress and Admin can cancel or requeue active rows. Stop those writers
  # before using queue state to decide that the worker can be stopped.
  if (( ${#writers[@]} )); then
    restore_containers=1
    timeout 30 docker stop --time 15 "${writers[@]}" >/dev/null
  fi
  for service in ingress admin log-sync; do
    ids="$(production_containers "$service")"
    if [[ -n "$ids" ]]; then
      echo "Greenfield $service is still running." >&2
      return 1
    fi
  done
  wait_for_drain
  if (( ${#workers[@]} )); then
    restore_containers=1
    timeout 30 docker stop --time 15 "${workers[@]}" >/dev/null
  fi
  worker_id="$(production_containers pi-agent)"
  if [[ -n "$worker_id" ]]; then
    echo 'Greenfield Pi Agent is still running.' >&2
    return 1
  fi
}

recover_production() {
  if [[ "$release_change_started" == 1 ]]; then
    echo 'Release change failed. Retry the deployment or use the guarded rollback entrypoint.' >&2
    if [[ "$claim_fenced" == 1 ]]; then
      echo 'The database claim fence is still in place.' >&2
    fi
    return
  fi
  if [[ "$restore_containers" == 1 ]] && ! timeout 30 docker start "${original_containers[@]}" >/dev/null; then
    echo 'Could not restart the original Greenfield containers. The claim fence remains in place.' >&2
    return
  fi
  if [[ "$clear_fence_on_failure" == 1 ]] && ! set_claim_fence false; then
    echo 'Could not remove the Greenfield claim fence. Retry the deployment or rollback to recover.' >&2
  fi
}

release_claim_fence() {
  if [[ "$claim_fenced" == 1 ]]; then
    set_claim_fence false
    claim_fenced=0
  fi
}

configure_production_compose() {
  compose=(docker compose --project-name casework-greenfield --env-file "$RUNTIME_ENV" -f "$COMPOSE_FILE")
  services=(ingress admin)
  if grep -Eq '^CASEWORK_LOG_SYNC_ENABLED="?true"?$' "$RUNTIME_ENV"; then
    compose+=(--profile log-sync)
    services+=(log-sync)
  fi
  "${compose[@]}" config --quiet
}

wait_ready() {
  local name="$1" url="$2" attempt
  for attempt in {1..60}; do
    if curl -fsS --max-time 3 "$url" >/dev/null; then
      printf '%s ready\n' "$name"
      return 0
    fi
    sleep 3
  done
  echo "${name} did not become ready" >&2
  return 1
}

original_containers=()
postgres_id=""
claim_fenced=0
clear_fence_on_failure=0
restore_containers=0
release_change_started=0
