from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "deploy" / "deploy-ghcr.sh"
ROLLBACK_SCRIPT = ROOT / "deploy" / "rollback-release.sh"
SYSTEM_BASH = Path("/bin/bash")
REVISION = "a" * 40
TAG = f"sha-{REVISION}"
DIGEST = f"sha256:{'d' * 64}"
IMAGE = "ghcr.io/example/zendesk-saas-agent"
REMOTE_IMAGE = f"{IMAGE}@{DIGEST}"
REVISION_IMAGE = f"{IMAGE}:{TAG}"
LOCAL_IMAGE = "zd-agent:latest"
PREVIOUS_IMAGE_ID = f"sha256:{'1' * 64}"
TARGET_IMAGE_ID = f"sha256:{'2' * 64}"
PREVIOUS_REVISION = "b" * 40


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _fake_command_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls_log = tmp_path / "calls.log"

    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'docker' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"

case "${1:-}" in
  pull|login)
    exit 0
    ;;
  tag)
    if [[ "${FAKE_TAG_FAIL:-0}" == "1" ]] && \
      [[ "${2:-}" == "$FAKE_REMOTE_IMAGE" ]]; then
      exit 71
    fi
    if [[ "${2:-}" == "$FAKE_REMOTE_IMAGE" ]] && \
      [[ "${3:-}" == "$FAKE_LOCAL_IMAGE" ]]; then
      printf '%s\n' target > "$FAKE_LOCAL_STATE_FILE"
    elif [[ "${2:-}" == "$FAKE_TARGET_IMAGE_ID" ]] && \
      [[ "${3:-}" == "$FAKE_LOCAL_IMAGE" ]]; then
      printf '%s\n' target > "$FAKE_LOCAL_STATE_FILE"
    elif [[ "${2:-}" == "$FAKE_PREVIOUS_IMAGE_ID" ]]; then
      printf '%s\n' previous > "$FAKE_LOCAL_STATE_FILE"
    fi
    exit 0
    ;;
  run)
    env_file=""
    resources_dir=""
    container_name=""
    previous=""
    for argument in "$@"; do
      if [[ "$previous" == "--env-file" ]]; then
        env_file="$argument"
      fi
      if [[ "$previous" == "--name" ]]; then
        container_name="$argument"
      fi
      case "$argument" in
        *:/opt/zd-agent/resources:ro)
          resources_dir="${argument%:/opt/zd-agent/resources:ro}"
          ;;
      esac
      previous="$argument"
    done
    printf '%s' "$env_file" > "$FAKE_PREFLIGHT_ENV_PATH_CAPTURE"
    printf '%s' "$resources_dir" > "$FAKE_PREFLIGHT_RESOURCES_PATH_CAPTURE"
    printf '%s' "$container_name" > "$FAKE_PREFLIGHT_CONTAINER_CAPTURE"
    /bin/cp "$env_file" "$FAKE_PREFLIGHT_ENV_CAPTURE"
    line_count="$(wc -l < "$env_file" | tr -d ' ')"
    if [[ "$line_count" != "3" ]] || \
      ! grep -qx 'ZENDESK_BROWSER_RELAY_URL=https://relay.example.test/mcp' "$env_file" || \
      ! grep -qx 'ZENDESK_BROWSER_RELAY_API_KEY=relay-secret' "$env_file" || \
      ! grep -qx 'ZENDESK_FRESHNESS_CALLBACK_URL=https://relay.example.test/webhook/relay-pre-send-freshness' "$env_file"; then
      printf '%s\n' 'preflight received an unsafe or incomplete env file' >&2
      exit 79
    fi
    command_text="$*"
    after_store="${command_text#*AdminConfigStore}"
    after_apply="${after_store#*.apply_saved}"
    if [[ "$after_store" == "$command_text" ]] || \
      [[ "$after_apply" == "$after_store" ]] || \
      [[ "$after_apply" != *"validate_public_reply_transport_config"* ]]; then
      printf '%s\n' 'preflight did not apply admin config before validation' >&2
      exit 80
    fi
    if [[ "${FAKE_PREFLIGHT_SIGNAL:-0}" == "1" ]]; then
      kill -TERM "$PPID"
      exit 143
    fi
    if [[ "${FAKE_PREFLIGHT_CONFIG_INVALID:-0}" == "1" ]]; then
      printf '%s\n' 'effective public reply config is invalid' >&2
      exit 78
    fi
    exit 0
    ;;
  rm)
    exit 0
    ;;
  exec)
    if [[ "$*" != *"apply_saved_admin_config"* ]] || \
      [[ "$*" != *"validate_public_reply_transport_config"* ]]; then
      exit 81
    fi
    if [[ "${FAKE_RUNNING_CONFIG_INVALID:-0}" == "1" ]]; then
      printf '%s\n' 'effective public reply config is invalid' >&2
      exit 78
    fi
    exit 0
    ;;
  inspect)
    if [[ "$(cat "$FAKE_ACTIVE_STATE_FILE")" != "active" ]]; then
      exit 1
    fi
    inspect_count=0
    [[ -f "$FAKE_INSPECT_COUNT_FILE" ]] && inspect_count="$(cat "$FAKE_INSPECT_COUNT_FILE")"
    inspect_count=$((inspect_count + 1))
    printf '%s\n' "$inspect_count" > "$FAKE_INSPECT_COUNT_FILE"
    state="$(cat "$FAKE_RUNTIME_STATE_FILE")"
    if [[ "${FAKE_ROLLBACK_IMAGE_MISMATCH:-0}" == "1" ]] && \
      [[ "$inspect_count" -ge 2 ]]; then
      printf '%s\n' "$FAKE_TARGET_IMAGE_ID"
    elif [[ "${FAKE_DEPLOYED_IMAGE_MISMATCH:-0}" == "1" ]] && \
      [[ "$state" == "target" ]]; then
      printf '%s\n' "$FAKE_PREVIOUS_IMAGE_ID"
    elif [[ "$state" == "target" ]]; then
      printf '%s\n' "$FAKE_TARGET_IMAGE_ID"
    else
      printf '%s\n' "$FAKE_PREVIOUS_IMAGE_ID"
    fi
    ;;
  image)
    case "${2:-}" in
      inspect)
        ref="${3:-}"
        if [[ "$*" == *'org.opencontainers.image.revision'* ]]; then
          if [[ "$ref" == "$FAKE_PREVIOUS_IMAGE_ID" ]]; then
            printf '%s\n' "$FAKE_PREVIOUS_REVISION"
          else
            printf '%s\n' "${FAKE_IMAGE_REVISION:-$FAKE_EXPECTED_REVISION}"
          fi
        elif [[ "$*" == *'{{.Created}}'* ]]; then
          case "$ref" in
            *:main|*:sha-current)
              printf '%s\n' '2026-07-17T08:00:00Z'
              ;;
            *:sha-previous)
              printf '%s\n' '2026-07-16T08:00:00Z'
              ;;
            *:sha-old)
              printf '%s\n' '2026-07-01T08:00:00Z'
              ;;
            *)
              printf '%s\n' '2026-06-01T08:00:00Z'
              ;;
          esac
        elif [[ "$ref" == "$FAKE_LOCAL_IMAGE" ]] && \
          [[ "$(cat "$FAKE_LOCAL_STATE_FILE")" == "previous" ]]; then
          printf '%s\n' "$FAKE_PREVIOUS_IMAGE_ID"
        else
          printf '%s\n' "$FAKE_TARGET_IMAGE_ID"
        fi
        ;;
      ls)
        if [[ "${FAKE_IMAGE_LS_FAIL:-0}" == "1" ]]; then
          exit 1
        fi
        printf '%s\n' \
          "$FAKE_IMAGE:sha-old sha256:${FAKE_OLD_IMAGE_HEX}" \
          "$FAKE_IMAGE:main $FAKE_TARGET_IMAGE_ID" \
          'ghcr.io/example/unrelated:latest sha256:4444444444444444444444444444444444444444444444444444444444444444' \
          "$FAKE_IMAGE:sha-previous $FAKE_PREVIOUS_IMAGE_ID" \
          "$FAKE_IMAGE:sha-current $FAKE_TARGET_IMAGE_ID"
        ;;
      rm)
        if [[ "${FAKE_IMAGE_RM_FAIL:-0}" == "1" ]]; then
          exit 1
        fi
        exit 0
        ;;
      *)
        printf 'unexpected docker image command: %s\n' "${2:-}" >&2
        exit 64
        ;;
    esac
    ;;
  ps)
    if [[ "$(cat "$FAKE_ACTIVE_STATE_FILE")" == "active" ]]; then
      printf '%s\n' zd-agent-container
    fi
    ;;
  *)
    printf 'unexpected docker command: %s\n' "${1:-}" >&2
    exit 64
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "install",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'install' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
count=0
[[ -f "$FAKE_INSTALL_COUNT_FILE" ]] && count="$(cat "$FAKE_INSTALL_COUNT_FILE")"
count=$((count + 1))
printf '%s\n' "$count" > "$FAKE_INSTALL_COUNT_FILE"
if [[ -n "${FAKE_INSTALL_FAIL_AT:-}" ]] && \
  [[ "$count" == "$FAKE_INSTALL_FAIL_AT" ]]; then
  exit 72
fi
if [[ "${FAKE_INSTALL_FAIL:-0}" == "1" ]] && [[ "$count" == "1" ]]; then
  exit 72
fi
/usr/bin/install "$@"
""",
    )
    _write_executable(
        fake_bin / "mv",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'mv' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
if [[ "${FAKE_BUNDLE_MV_FAIL:-0}" == "1" ]] && \
  [[ "${*: -1}" == *"/.release-rollbacks/$FAKE_PREVIOUS_REVISION" ]]; then
  exit 76
fi
exec /bin/mv "$@"
""",
    )
    _write_executable(
        fake_bin / "date",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${FAKE_DATE_FAIL:-0}" == "1" ]]; then
  exit 77
fi
exec /bin/date "$@"
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
case "${1:-}" in
  daemon-reload)
    count=0
    [[ -f "$FAKE_DAEMON_COUNT_FILE" ]] && count="$(cat "$FAKE_DAEMON_COUNT_FILE")"
    count=$((count + 1))
    printf '%s\n' "$count" > "$FAKE_DAEMON_COUNT_FILE"
    if [[ "${FAKE_DAEMON_RELOAD_FAIL:-0}" == "1" ]] && [[ "$count" == "1" ]]; then
      exit 73
    fi
    ;;
  restart)
    count=0
    [[ -f "$FAKE_RESTART_COUNT_FILE" ]] && count="$(cat "$FAKE_RESTART_COUNT_FILE")"
    count=$((count + 1))
    printf '%s\n' "$count" > "$FAKE_RESTART_COUNT_FILE"
    if [[ "${FAKE_RESTART_FAIL:-0}" == "1" ]] && [[ "$count" == "1" ]]; then
      exit 74
    fi
    /bin/cp "$FAKE_LOCAL_STATE_FILE" "$FAKE_RUNTIME_STATE_FILE"
    printf '%s\n' active > "$FAKE_ACTIVE_STATE_FILE"
    if [[ "$(cat "$FAKE_RUNTIME_STATE_FILE")" == "target" ]] && \
      [[ "${FAKE_TARGET_MUTATES_STATE:-0}" == "1" ]]; then
      printf '%s\n' '{"2812":{"state":"awaiting_customer","state_revision":2}}' \
        > "$FAKE_STATE_DIR/tickets.json"
      printf '%s\n' 'v2-outbox' > "$FAKE_STATE_DIR/casework_effects.sqlite3"
    fi
    ;;
  stop)
    printf '%s\n' inactive > "$FAKE_ACTIVE_STATE_FILE"
    ;;
  is-active)
    [[ "$(cat "$FAKE_ACTIVE_STATE_FILE")" == "active" ]] || exit 3
    if [[ "${FAKE_ROLLBACK_HEALTH_FAIL:-0}" == "1" ]] && \
      [[ "$(cat "$FAKE_RUNTIME_STATE_FILE")" == "previous" ]]; then
      exit 3
    fi
    ;;
  show)
    if [[ "$(cat "$FAKE_ACTIVE_STATE_FILE")" == "active" ]]; then
      printf '%s\n' active
    else
      printf '%s\n' inactive
    fi
    ;;
  status)
    ;;
  *)
    exit 64
    ;;
esac
exit 0
""",
    )
    _write_executable(
        fake_bin / "systemd-analyze",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'systemd-analyze' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
if [[ "${FAKE_VERIFY_SIGNAL:-0}" == "1" ]]; then
  kill -TERM "$PPID"
  exit 143
fi
if [[ "${FAKE_VERIFY_FAIL:-0}" == "1" ]]; then
  exit 75
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "git",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'git' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
if [[ "$*" == *"rev-parse HEAD"* ]]; then
  printf '%s\n' "${FAKE_SOURCE_REVISION:-$FAKE_EXPECTED_REVISION}"
  exit 0
fi
if [[ "$*" == *"diff"*"--quiet"* ]]; then
  [[ "${FAKE_GIT_DIRTY:-0}" == "1" ]] && exit 1
  exit 0
fi
if [[ "${1:-}" == "-C" ]] && [[ "${3:-}" == "show" ]]; then
  if [[ "${4:-}" != "$FAKE_PREVIOUS_REVISION:config.yaml" ]]; then
    exit 65
  fi
  /bin/cat "$FAKE_PREVIOUS_CONFIG_FILE"
  exit 0
fi
if [[ "${1:-}" == "-C" ]] && [[ "${3:-}" == "checkout" ]] && \
  [[ "${4:-}" == "--detach" ]] && [[ "${5:-}" == "$FAKE_PREVIOUS_REVISION" ]]; then
  /bin/cp "$FAKE_PREVIOUS_CONFIG_FILE" "$ZD_AGENT_CONFIG_FILE"
  printf '%s\n' "$FAKE_PREVIOUS_REVISION" > "$FAKE_CHECKOUT_REVISION_FILE"
  exit 0
fi
if [[ "${1:-}" == "-C" ]] && [[ "${3:-}" == "checkout" ]] && \
  [[ "${4:-}" == "--detach" ]] && [[ "${5:-}" == "$FAKE_EXPECTED_REVISION" ]]; then
  /bin/cp "$FAKE_TARGET_CONFIG_FILE" "$ZD_AGENT_CONFIG_FILE"
  printf '%s\n' "$FAKE_EXPECTED_REVISION" > "$FAKE_CHECKOUT_REVISION_FILE"
  exit 0
fi
exit 64
""",
    )
    _write_executable(
        fake_bin / "tar",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'tar' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
if [[ "$*" == *"-cpf"* ]] && [[ "${FAKE_TAR_SIGNAL:-0}" == "1" ]]; then
  kill -TERM "$PPID"
  exit 143
fi
if [[ "$*" == *"-cpf"* ]] && [[ "${FAKE_TAR_FAIL:-0}" == "1" ]]; then
  previous=""
  for argument in "$@"; do
    if [[ "$previous" == "-cpf" ]]; then
      printf '%s\n' partial > "$argument"
      break
    fi
    previous="$argument"
  done
  exit 77
fi
exec /usr/bin/tar "$@"
""",
    )
    _write_executable(
        fake_bin / "timeout",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'timeout' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
if [[ "${1:-}" == "-k" ]]; then
  shift 2
fi
shift
exec "$@"
""",
    )
    _write_executable(
        fake_bin / "flock",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'flock' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
[[ "${FAKE_FLOCK_FAIL:-0}" == "1" ]] && exit 1
exit 0
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'curl' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"

if [[ "$*" == *"/webhook/relay-pre-send-freshness"* ]]; then
  printf '%s' "${FAKE_FRESHNESS_STATUS:-401}"
  exit 0
fi
runtime_state="$(cat "$FAKE_RUNTIME_STATE_FILE")"
if [[ "${FAKE_HEALTH_FAIL:-0}" == "1" ]] && [[ "$runtime_state" == "target" ]]; then
  exit 22
fi
if [[ "${FAKE_ROLLBACK_HEALTH_FAIL:-0}" == "1" ]] && [[ "$runtime_state" == "previous" ]]; then
  exit 22
fi
printf 'ok'
""",
    )
    _write_executable(
        fake_bin / "sleep",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'sleep' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
""",
    )

    env = os.environ.copy()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=must-not-reach-preflight\n"
        "ZENDESK_BROWSER_RELAY_URL=https://relay.example.test/mcp\n"
        "ZENDESK_BROWSER_RELAY_API_KEY=relay-secret\n"
        "ZENDESK_FRESHNESS_CALLBACK_URL="
        "https://relay.example.test/webhook/relay-pre-send-freshness\n"
    )
    local_state = tmp_path / "local-image-state"
    runtime_state = tmp_path / "runtime-image-state"
    local_state.write_text("previous\n")
    runtime_state.write_text("previous\n")
    active_state = tmp_path / "service-active-state"
    active_state.write_text("active\n")
    resources_dir = tmp_path / "resources"
    state_dir = resources_dir / ".state"
    state_dir.mkdir(parents=True)
    (state_dir / "active_instance_id").write_text("test-instance\n")
    (state_dir / "admin_overrides.yaml").write_text(
        "public_reply:\n  enabled: true\nllm:\n  model: must-not-copy\n"
    )
    (state_dir / "admin_secrets.yaml").write_text(
        "public_reply:\n  browser_relay_api_key: relay-secret\n"
        "openai:\n  api_key: must-not-copy\n"
    )
    (state_dir / "tickets.json").write_text(
        '{"2812":{"state":"waiting_for_issue","state_revision":1}}\n'
    )
    (state_dir / "casework_effects.sqlite3").write_text("legacy-outbox\n")
    config_file = tmp_path / "config.yaml"
    config_file.write_text("agent:\n  legacy_target_checkout: true\n")
    target_config = tmp_path / "target-config.yaml"
    target_config.write_bytes(config_file.read_bytes())
    previous_config = tmp_path / "previous-config.yaml"
    previous_config.write_text("agent:\n  guardrails:\n    max_rounds: 30\n")
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_CALLS_LOG": str(calls_log),
            "FAKE_EXPECTED_REVISION": REVISION,
            "FAKE_IMAGE": IMAGE,
            "FAKE_REMOTE_IMAGE": REMOTE_IMAGE,
            "FAKE_REVISION_IMAGE": REVISION_IMAGE,
            "FAKE_LOCAL_IMAGE": LOCAL_IMAGE,
            "FAKE_PREVIOUS_IMAGE_ID": PREVIOUS_IMAGE_ID,
            "FAKE_TARGET_IMAGE_ID": TARGET_IMAGE_ID,
            "FAKE_OLD_IMAGE_HEX": "3" * 64,
            "FAKE_LOCAL_STATE_FILE": str(local_state),
            "FAKE_RUNTIME_STATE_FILE": str(runtime_state),
            "FAKE_ACTIVE_STATE_FILE": str(active_state),
            "FAKE_STATE_DIR": str(state_dir),
            "FAKE_PREVIOUS_REVISION": PREVIOUS_REVISION,
            "FAKE_PREVIOUS_CONFIG_FILE": str(previous_config),
            "FAKE_TARGET_CONFIG_FILE": str(target_config),
            "FAKE_CHECKOUT_REVISION_FILE": str(tmp_path / "checkout-revision"),
            "FAKE_INSTALL_COUNT_FILE": str(tmp_path / "install-count"),
            "FAKE_DAEMON_COUNT_FILE": str(tmp_path / "daemon-count"),
            "FAKE_RESTART_COUNT_FILE": str(tmp_path / "restart-count"),
            "FAKE_INSPECT_COUNT_FILE": str(tmp_path / "inspect-count"),
            "FAKE_PREFLIGHT_ENV_CAPTURE": str(tmp_path / "preflight.env"),
            "FAKE_PREFLIGHT_ENV_PATH_CAPTURE": str(tmp_path / "preflight-env-path"),
            "FAKE_PREFLIGHT_RESOURCES_PATH_CAPTURE": str(
                tmp_path / "preflight-resources-path"
            ),
            "FAKE_PREFLIGHT_CONTAINER_CAPTURE": str(
                tmp_path / "preflight-container-name"
            ),
            "ZD_AGENT_IMAGE": IMAGE,
            "ZD_AGENT_IMAGE_DIGEST": DIGEST,
            "ZD_AGENT_IMAGE_KEEP_RECENT": "2",
            "ZD_AGENT_HEALTH_TIMEOUT_SECONDS": "1",
            "ZD_AGENT_ENV_FILE": str(env_file),
            "ZD_AGENT_CONFIG_FILE": str(config_file),
            "ZD_AGENT_RESOURCES_DIR": str(resources_dir),
            "ZD_AGENT_INSTANCE_ID": "test-instance",
            "ZD_AGENT_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
            "ZD_AGENT_SYSTEMD_UNIT_TARGET": str(tmp_path / "zd-agent.service"),
        }
    )
    Path(env["ZD_AGENT_SYSTEMD_UNIT_TARGET"]).write_text("legacy systemd unit\n")
    return env, calls_log


def _run_deploy(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SYSTEM_BASH), str(DEPLOY_SCRIPT), TAG],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_rollback(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SYSTEM_BASH), str(ROLLBACK_SCRIPT), PREVIOUS_REVISION],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _next_call(calls: list[str], prefix: str, *, after: int = -1) -> int:
    return next(
        index
        for index, call in enumerate(calls)
        if index > after and call.startswith(prefix)
    )


def _assert_successful_rollback(
    *,
    env: dict[str, str],
    calls: list[str],
    failure_index: int,
) -> None:
    rollback_tag_index = _next_call(
        calls,
        f"docker tag {PREVIOUS_IMAGE_ID} {LOCAL_IMAGE}",
        after=failure_index,
    )
    rollback_install_index = _next_call(calls, "install ", after=rollback_tag_index)
    rollback_reload_index = _next_call(
        calls, "systemctl daemon-reload", after=rollback_install_index
    )
    rollback_restart_index = _next_call(
        calls, "systemctl restart zd-agent", after=rollback_reload_index
    )
    rollback_active_index = _next_call(
        calls, "systemctl is-active --quiet zd-agent", after=rollback_restart_index
    )
    rollback_health_index = _next_call(calls, "curl ", after=rollback_active_index)
    rollback_inspect_index = _next_call(
        calls, "docker inspect zd-agent ", after=rollback_health_index
    )
    assert (
        failure_index
        < rollback_tag_index
        < rollback_install_index
        < rollback_reload_index
        < rollback_restart_index
        < rollback_active_index
        < rollback_health_index
        < rollback_inspect_index
    )
    assert Path(env["FAKE_LOCAL_STATE_FILE"]).read_text().strip() == "previous"
    assert Path(env["FAKE_RUNTIME_STATE_FILE"]).read_text().strip() == "previous"
    unit_target = Path(env["ZD_AGENT_SYSTEMD_UNIT_TARGET"])
    assert unit_target.read_text() == "legacy systemd unit\n"
    assert not list(unit_target.parent.glob(f"{unit_target.name}.deploy-backup.*"))


def test_successful_deploy_prunes_only_unprotected_historical_agent_images(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    result = _run_deploy(env)

    assert result.returncode == 0, result.stderr
    calls = calls_log.read_text().splitlines()
    assert "docker image rm ghcr.io/example/zendesk-saas-agent:sha-old" in calls
    assert not any("sha-current" in call and " image rm " in call for call in calls)
    assert not any("sha-previous" in call and " image rm " in call for call in calls)
    assert not any("unrelated" in call and " image rm " in call for call in calls)
    assert not any("docker image prune" in call for call in calls)
    assert not any("docker system prune" in call for call in calls)

    freshness_index = next(
        index
        for index, call in enumerate(calls)
        if "/webhook/relay-pre-send-freshness" in call
    )
    prune_index = calls.index(
        "docker image rm ghcr.io/example/zendesk-saas-agent:sha-old"
    )
    assert prune_index > freshness_index

    lock_index = calls.index("flock -w 120 9")
    source_revision_index = _next_call(calls, "git -C ")
    pull_index = calls.index(f"docker pull {REMOTE_IMAGE}")
    revision_check_index = _next_call(calls, f"docker image inspect {REMOTE_IMAGE} ")
    timeout_index = _next_call(calls, "timeout -k 5s 30s docker run ")
    preflight_index = _next_call(calls, "docker run ")
    preflight_cleanup_index = _next_call(calls, "docker rm -f ", after=preflight_index)
    revision_tag_index = calls.index(f"docker tag {REMOTE_IMAGE} {REVISION_IMAGE}")
    local_tag_index = calls.index(f"docker tag {REMOTE_IMAGE} {LOCAL_IMAGE}")
    verify_index = _next_call(calls, "systemd-analyze verify ")
    install_index = _next_call(calls, "install ")
    daemon_reload_index = calls.index("systemctl daemon-reload")
    restart_index = calls.index("systemctl restart zd-agent")
    active_index = _next_call(calls, "systemctl is-active --quiet zd-agent")
    health_index = _next_call(calls, "curl ", after=active_index)
    running_check_index = _next_call(calls, "docker exec ")
    assert (
        lock_index
        < source_revision_index
        < pull_index
        < revision_check_index
        < timeout_index
        < preflight_index
        < preflight_cleanup_index
        < revision_tag_index
        < local_tag_index
        < verify_index
        < install_index
        < daemon_reload_index
        < restart_index
        < active_index
        < health_index
        < running_check_index
        < freshness_index
        < prune_index
    )
    preflight_call = calls[preflight_index]
    assert "--network none" in preflight_call
    assert "--read-only" in preflight_call
    assert "--cap-drop ALL" in preflight_call
    assert "no-new-privileges" in preflight_call
    assert "--pids-limit 64" in preflight_call
    assert "--log-driver none" in preflight_call
    assert "--env-file " in preflight_call
    assert str(env["ZD_AGENT_ENV_FILE"]) not in preflight_call
    assert f"{env['ZD_AGENT_CONFIG_FILE']}:/app/config.yaml:ro" in preflight_call
    preflight_resources = Path(
        Path(env["FAKE_PREFLIGHT_RESOURCES_PATH_CAPTURE"]).read_text()
    )
    assert f"{preflight_resources}:/opt/zd-agent/resources:ro" in preflight_call
    assert REMOTE_IMAGE in preflight_call
    assert (
        preflight_call.index("AdminConfigStore")
        < preflight_call.index("apply_saved")
        < preflight_call.rindex("validate_public_reply_transport_config")
    )

    expected_env = {
        "ZENDESK_BROWSER_RELAY_URL=https://relay.example.test/mcp",
        "ZENDESK_BROWSER_RELAY_API_KEY=relay-secret",
        "ZENDESK_FRESHNESS_CALLBACK_URL="
        "https://relay.example.test/webhook/relay-pre-send-freshness",
    }
    assert set(Path(env["FAKE_PREFLIGHT_ENV_CAPTURE"]).read_text().splitlines()) == (
        expected_env
    )
    assert len(Path(env["FAKE_PREFLIGHT_ENV_CAPTURE"]).read_text().splitlines()) == 3
    preflight_env = Path(Path(env["FAKE_PREFLIGHT_ENV_PATH_CAPTURE"]).read_text())
    preflight_container = Path(env["FAKE_PREFLIGHT_CONTAINER_CAPTURE"]).read_text()
    assert preflight_container.startswith("zd-agent-preflight-")
    assert calls[preflight_cleanup_index] == f"docker rm -f {preflight_container}"
    assert not preflight_env.exists()
    assert not preflight_resources.exists()
    assert Path(env["FAKE_LOCAL_STATE_FILE"]).read_text().strip() == "target"
    assert Path(env["FAKE_RUNTIME_STATE_FILE"]).read_text().strip() == "target"


def test_deploy_refuses_a_host_that_does_not_own_the_instance_fence(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    marker = Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state" / "active_instance_id"
    marker.write_text("different-host\n")

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "does not own the Agent instance fence" in result.stderr
    assert not calls_log.exists()


def test_image_cleanup_protects_every_retained_rollback_bundle_image(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["ZD_AGENT_IMAGE_KEEP_RECENT"] = "1"
    rollback_dir = Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".release-rollbacks"
    historical = rollback_dir / ("c" * 40)
    historical.mkdir(parents=True)
    historical.joinpath("manifest").write_text(
        f"previous_image_id=sha256:{env['FAKE_OLD_IMAGE_HEX']}\n",
    )

    result = _run_deploy(env)

    assert result.returncode == 0, result.stderr
    calls = calls_log.read_text().splitlines()
    assert not any("sha-old" in call and " image rm " in call for call in calls)


def test_successful_deploy_retains_downgrade_safe_previous_release_bundle(
    tmp_path: Path,
) -> None:
    env, _calls_log = _fake_command_environment(tmp_path)
    original_tickets = (
        Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state" / "tickets.json"
    ).read_bytes()

    result = _run_deploy(env)

    assert result.returncode == 0, result.stderr
    bundle = (
        Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".release-rollbacks" / PREVIOUS_REVISION
    )
    assert bundle.stat().st_mode & 0o777 == 0o700
    assert (bundle / "config.yaml").read_text() == (
        "agent:\n  guardrails:\n    max_rounds: 30\n"
    )
    assert (bundle / "zd-agent.service").read_text() == "legacy systemd unit\n"
    manifest = (bundle / "manifest").read_text()
    assert f"previous_revision={PREVIOUS_REVISION}" in manifest
    assert f"previous_image_id={PREVIOUS_IMAGE_ID}" in manifest
    for name in ("state.tar", "config.yaml", "zd-agent.service", "manifest"):
        assert (bundle / name).stat().st_mode & 0o777 == 0o600
    with tarfile.open(bundle / "state.tar") as archive:
        member = archive.extractfile("./tickets.json")
        assert member is not None
        assert member.read() == original_tickets


def test_post_canary_rollback_consumes_retained_bundle_atomically(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    state_dir = Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state"
    original_tickets = (state_dir / "tickets.json").read_bytes()
    original_outbox = (state_dir / "casework_effects.sqlite3").read_bytes()
    deployed = _run_deploy(env)
    assert deployed.returncode == 0, deployed.stderr

    (state_dir / "tickets.json").write_text(
        '{"2812":{"state":"ownership_pending","state_revision":9}}\n'
    )
    (state_dir / "casework_effects.sqlite3").write_text("v2-canary-effects\n")

    rolled_back = _run_rollback(env)

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert (state_dir / "tickets.json").read_bytes() == original_tickets
    assert (state_dir / "casework_effects.sqlite3").read_bytes() == original_outbox
    assert Path(env["ZD_AGENT_CONFIG_FILE"]).read_text() == (
        "agent:\n  guardrails:\n    max_rounds: 30\n"
    )
    assert Path(env["ZD_AGENT_SYSTEMD_UNIT_TARGET"]).read_text() == (
        "legacy systemd unit\n"
    )
    assert Path(env["FAKE_RUNTIME_STATE_FILE"]).read_text().strip() == "previous"
    assert Path(env["FAKE_CHECKOUT_REVISION_FILE"]).read_text().strip() == (
        PREVIOUS_REVISION
    )
    assert (
        Path(env["ZD_AGENT_RESOURCES_DIR"])
        / ".release-rollbacks"
        / PREVIOUS_REVISION
        / "state.tar"
    ).exists()
    calls = calls_log.read_text().splitlines()
    assert calls.count("systemctl stop zd-agent") >= 2
    assert calls.count("systemctl restart zd-agent") >= 2


def test_failed_post_canary_rollback_restores_forward_release(tmp_path: Path) -> None:
    env, _calls_log = _fake_command_environment(tmp_path)
    deployed = _run_deploy(env)
    assert deployed.returncode == 0, deployed.stderr
    state_dir = Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state"
    target_tickets = b'{"2812":{"state":"processed","state_revision":10}}\n'
    target_outbox = b"target-v2-effects\n"
    (state_dir / "tickets.json").write_bytes(target_tickets)
    (state_dir / "casework_effects.sqlite3").write_bytes(target_outbox)
    target_config = Path(env["ZD_AGENT_CONFIG_FILE"]).read_bytes()
    target_unit = Path(env["ZD_AGENT_SYSTEMD_UNIT_TARGET"]).read_bytes()
    env["FAKE_ROLLBACK_HEALTH_FAIL"] = "1"

    rolled_back = _run_rollback(env)

    assert rolled_back.returncode != 0
    assert (state_dir / "tickets.json").read_bytes() == target_tickets
    assert (state_dir / "casework_effects.sqlite3").read_bytes() == target_outbox
    assert Path(env["ZD_AGENT_CONFIG_FILE"]).read_bytes() == target_config
    assert Path(env["ZD_AGENT_SYSTEMD_UNIT_TARGET"]).read_bytes() == target_unit
    assert Path(env["FAKE_RUNTIME_STATE_FILE"]).read_text().strip() == "target"
    assert Path(env["FAKE_CHECKOUT_REVISION_FILE"]).read_text().strip() == REVISION
    assert "Failed rollback was reversed" in rolled_back.stderr


def test_post_canary_rollback_works_when_target_container_is_absent(
    tmp_path: Path,
) -> None:
    env, _calls_log = _fake_command_environment(tmp_path)
    deployed = _run_deploy(env)
    assert deployed.returncode == 0, deployed.stderr
    Path(env["FAKE_ACTIVE_STATE_FILE"]).write_text("inactive\n")

    rolled_back = _run_rollback(env)

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert Path(env["FAKE_RUNTIME_STATE_FILE"]).read_text().strip() == "previous"
    assert Path(env["FAKE_CHECKOUT_REVISION_FILE"]).read_text().strip() == (
        PREVIOUS_REVISION
    )


def test_failed_health_check_never_prunes_images(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_HEALTH_FAIL"] = "1"
    result = _run_deploy(env)

    assert result.returncode != 0
    calls = calls_log.read_text().splitlines()
    failure_index = _next_call(calls, "curl ")
    _assert_successful_rollback(env=env, calls=calls, failure_index=failure_index)
    assert not any("docker image rm" in call for call in calls)
    assert not any("docker image prune" in call for call in calls)
    assert not any("docker system prune" in call for call in calls)
    assert "Deployment complete" not in result.stdout


def test_failed_target_health_restores_revision_config_and_pre_migration_state(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_HEALTH_FAIL"] = "1"
    env["FAKE_TARGET_MUTATES_STATE"] = "1"
    original_tickets = (
        Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state" / "tickets.json"
    ).read_bytes()
    original_outbox = (
        Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state" / "casework_effects.sqlite3"
    ).read_bytes()

    result = _run_deploy(env)

    assert result.returncode != 0
    state_dir = Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state"
    assert (state_dir / "tickets.json").read_bytes() == original_tickets
    assert (state_dir / "casework_effects.sqlite3").read_bytes() == original_outbox
    assert Path(env["ZD_AGENT_CONFIG_FILE"]).read_text() == (
        "agent:\n  guardrails:\n    max_rounds: 30\n"
    )
    assert Path(env["FAKE_CHECKOUT_REVISION_FILE"]).read_text().strip() == (
        PREVIOUS_REVISION
    )
    calls = calls_log.read_text().splitlines()
    stop_index = calls.index("systemctl stop zd-agent")
    target_restart = calls.index("systemctl restart zd-agent")
    rollback_restart = _next_call(
        calls,
        "systemctl restart zd-agent",
        after=target_restart,
    )
    assert stop_index < target_restart < rollback_restart
    assert not list(Path(env["ZD_AGENT_RESOURCES_DIR"]).glob(".state.deploy-backup.*"))


@pytest.mark.parametrize("failure_mode", ["FAKE_TAR_FAIL", "FAKE_TAR_SIGNAL"])
def test_snapshot_failure_never_restores_a_partial_archive(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    env, _calls_log = _fake_command_environment(tmp_path)
    env[failure_mode] = "1"
    state_dir = Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state"
    original = {
        path.relative_to(state_dir): path.read_bytes()
        for path in state_dir.rglob("*")
        if path.is_file()
    }

    result = _run_deploy(env)

    assert result.returncode != 0
    restored = {
        path.relative_to(state_dir): path.read_bytes()
        for path in state_dir.rglob("*")
        if path.is_file()
    }
    assert restored == original
    assert not list(Path(env["ZD_AGENT_RESOURCES_DIR"]).glob(".state.deploy-backup.*"))


@pytest.mark.parametrize(
    ("failure_name", "failure_value"),
    [
        ("FAKE_INSTALL_FAIL_AT", "2"),
        ("FAKE_INSTALL_FAIL_AT", "3"),
        ("FAKE_INSTALL_FAIL_AT", "4"),
        ("FAKE_DATE_FAIL", "1"),
        ("FAKE_BUNDLE_MV_FAIL", "1"),
    ],
    ids=["state-copy", "config-copy", "unit-copy", "manifest", "atomic-rename"],
)
def test_incomplete_retained_bundle_forces_automatic_rollback(
    tmp_path: Path,
    failure_name: str,
    failure_value: str,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env[failure_name] = failure_value
    original_state = (
        Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state" / "tickets.json"
    ).read_bytes()

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "Deployment complete" not in result.stdout
    assert "Could not retain downgrade-safe rollback bundle" in result.stderr
    assert (
        Path(env["ZD_AGENT_RESOURCES_DIR"]) / ".state" / "tickets.json"
    ).read_bytes() == original_state
    assert Path(env["FAKE_RUNTIME_STATE_FILE"]).read_text().strip() == "previous"
    assert "systemctl restart zd-agent" in calls_log.read_text().splitlines()


def test_invalid_retention_count_fails_before_deployment(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["ZD_AGENT_IMAGE_KEEP_RECENT"] = "0"

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "ZD_AGENT_IMAGE_KEEP_RECENT must be a positive integer" in result.stderr
    assert not calls_log.exists() or calls_log.read_text() == ""


def test_failed_freshness_check_never_prunes_images(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_FRESHNESS_STATUS"] = "500"

    result = _run_deploy(env)

    assert result.returncode != 0
    calls = calls_log.read_text().splitlines()
    failure_index = next(
        index
        for index, call in enumerate(calls)
        if "/webhook/relay-pre-send-freshness" in call
    )
    _assert_successful_rollback(env=env, calls=calls, failure_index=failure_index)
    assert not any("docker image ls" in call for call in calls)
    assert not any("docker image rm" in call for call in calls)


def test_running_image_mismatch_rolls_back_before_success(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_DEPLOYED_IMAGE_MISMATCH"] = "1"

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "image does not match the target digest" in result.stderr
    assert "Deployment complete" not in result.stdout
    calls = calls_log.read_text().splitlines()
    freshness_index = _next_call(
        calls,
        "curl ",
        after=_next_call(calls, "docker exec zd-agent "),
    )
    running_inspect_index = _next_call(
        calls,
        "docker inspect zd-agent ",
        after=freshness_index,
    )
    _assert_successful_rollback(
        env=env,
        calls=calls,
        failure_index=running_inspect_index,
    )
    assert "Deployment complete" not in result.stdout


def test_deploy_syncs_versioned_systemd_unit_before_restart(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)

    result = _run_deploy(env)

    assert result.returncode == 0, result.stderr
    unit_target = Path(env["ZD_AGENT_SYSTEMD_UNIT_TARGET"])
    assert (
        unit_target.read_text()
        == (ROOT / "deploy" / "systemd" / "zd-agent.service").read_text()
    )

    calls = calls_log.read_text().splitlines()
    install_index = next(
        index for index, call in enumerate(calls) if call.startswith("install ")
    )
    daemon_reload_index = calls.index("systemctl daemon-reload")
    restart_index = calls.index("systemctl restart zd-agent")
    assert install_index < daemon_reload_index < restart_index


def test_invalid_effective_public_reply_config_fails_deployment(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_RUNNING_CONFIG_INVALID"] = "1"

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "effective public reply config" in result.stderr
    calls = calls_log.read_text().splitlines()
    failure_index = _next_call(calls, "docker exec zd-agent ")
    _assert_successful_rollback(env=env, calls=calls, failure_index=failure_index)
    assert not any("docker image ls" in call for call in calls)
    assert not any("docker image rm" in call for call in calls)
    assert calls.count("systemctl restart zd-agent") == 2
    assert "Deployment complete" not in result.stdout


@pytest.mark.parametrize(
    ("failure_flag", "expected_error"),
    [
        ("FAKE_ROLLBACK_HEALTH_FAIL", "Rollback service readiness check failed"),
        ("FAKE_ROLLBACK_IMAGE_MISMATCH", "not running the previous image"),
    ],
    ids=["health", "image-identity"],
)
def test_failed_rollback_readiness_preserves_unit_backup_for_manual_recovery(
    tmp_path: Path,
    failure_flag: str,
    expected_error: str,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_RUNNING_CONFIG_INVALID"] = "1"
    env[failure_flag] = "1"

    result = _run_deploy(env)

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert "Automatic rollback was incomplete" in result.stderr
    unit_target = Path(env["ZD_AGENT_SYSTEMD_UNIT_TARGET"])
    backups = list(unit_target.parent.glob(f"{unit_target.name}.deploy-backup.*"))
    assert len(backups) == 1
    calls = calls_log.read_text().splitlines()
    assert not any("docker image rm" in call for call in calls)
    assert "Deployment complete" not in result.stdout


def test_invalid_target_image_config_fails_before_service_restart(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_PREFLIGHT_CONFIG_INVALID"] = "1"

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "effective public reply config" in result.stderr
    calls = calls_log.read_text().splitlines()
    assert any(call.startswith("docker run --rm ") for call in calls)
    assert not any(call.startswith("docker tag ") for call in calls)
    assert not any(call == "systemctl restart zd-agent" for call in calls)
    assert not any("docker image ls" in call for call in calls)
    assert not any("docker image rm" in call for call in calls)
    preflight_env = Path(Path(env["FAKE_PREFLIGHT_ENV_PATH_CAPTURE"]).read_text())
    preflight_resources = Path(
        Path(env["FAKE_PREFLIGHT_RESOURCES_PATH_CAPTURE"]).read_text()
    )
    assert not preflight_env.exists()
    assert not preflight_resources.exists()


@pytest.mark.parametrize(
    ("failure_flag", "failure_prefix"),
    [
        ("FAKE_TAG_FAIL", f"docker tag {REMOTE_IMAGE} {REVISION_IMAGE}"),
        ("FAKE_VERIFY_FAIL", "systemd-analyze verify "),
        ("FAKE_INSTALL_FAIL", "install "),
        ("FAKE_DAEMON_RELOAD_FAIL", "systemctl daemon-reload"),
        ("FAKE_RESTART_FAIL", "systemctl restart zd-agent"),
    ],
    ids=["tag", "verify", "install", "daemon-reload", "restart"],
)
def test_mutation_failure_restores_previous_image_and_unit_in_order(
    tmp_path: Path,
    failure_flag: str,
    failure_prefix: str,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env[failure_flag] = "1"

    result = _run_deploy(env)

    assert result.returncode != 0
    calls = calls_log.read_text().splitlines()
    failure_index = _next_call(calls, failure_prefix)
    _assert_successful_rollback(env=env, calls=calls, failure_index=failure_index)
    assert not any("docker image ls" in call for call in calls)
    assert not any("docker image rm" in call for call in calls)
    assert "Deployment complete" not in result.stdout


def test_preflight_signal_cleans_container_and_temporary_files(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_PREFLIGHT_SIGNAL"] = "1"

    result = _run_deploy(env)

    assert result.returncode == 143
    calls = calls_log.read_text().splitlines()
    container_name = Path(env["FAKE_PREFLIGHT_CONTAINER_CAPTURE"]).read_text()
    assert f"docker rm -f {container_name}" in calls
    preflight_env = Path(Path(env["FAKE_PREFLIGHT_ENV_PATH_CAPTURE"]).read_text())
    preflight_resources = Path(
        Path(env["FAKE_PREFLIGHT_RESOURCES_PATH_CAPTURE"]).read_text()
    )
    assert not preflight_env.exists()
    assert not preflight_resources.exists()
    assert not any(call.startswith("docker tag ") for call in calls)
    assert not any(call == "systemctl restart zd-agent" for call in calls)
    assert Path(env["ZD_AGENT_SYSTEMD_UNIT_TARGET"]).read_text() == (
        "legacy systemd unit\n"
    )


def test_signal_after_rollback_is_armed_restores_previous_release(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_VERIFY_SIGNAL"] = "1"

    result = _run_deploy(env)

    assert result.returncode == 143
    calls = calls_log.read_text().splitlines()
    failure_index = _next_call(calls, "systemd-analyze verify ")
    _assert_successful_rollback(env=env, calls=calls, failure_index=failure_index)
    assert "Deployment complete" not in result.stdout


def test_deployment_lock_timeout_fails_before_inspection_or_pull(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_FLOCK_FAIL"] = "1"

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "Timed out waiting" in result.stderr
    assert calls_log.read_text().splitlines() == ["flock -w 120 9"]


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("FAKE_SOURCE_REVISION", "b" * 40),
        ("FAKE_GIT_DIRTY", "1"),
    ],
    ids=["mixed-source-revision", "dirty-worktree"],
)
def test_source_guard_fails_before_pull_or_preflight(
    tmp_path: Path,
    env_name: str,
    env_value: str,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env[env_name] = env_value

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "dirty or mixed-revision" in result.stderr
    calls = calls_log.read_text().splitlines()
    assert not any(call.startswith("docker pull ") for call in calls)
    assert not any(call.startswith("docker run ") for call in calls)


def test_image_revision_guard_fails_before_preflight_or_tag(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_IMAGE_REVISION"] = "b" * 40

    result = _run_deploy(env)

    assert result.returncode != 0
    assert "Target image revision does not match" in result.stderr
    calls = calls_log.read_text().splitlines()
    assert f"docker pull {REMOTE_IMAGE}" in calls
    assert not any(call.startswith("docker run ") for call in calls)
    assert not any(call.startswith("docker tag ") for call in calls)


def test_cleanup_failure_warns_without_failing_healthy_deploy(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_IMAGE_RM_FAIL"] = "1"

    result = _run_deploy(env)

    assert result.returncode == 0, result.stderr
    assert "Could not remove historical image" in result.stderr
    calls = calls_log.read_text().splitlines()
    assert "docker image rm ghcr.io/example/zendesk-saas-agent:sha-old" in calls


def test_image_list_failure_warns_without_failing_healthy_deploy(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_IMAGE_LS_FAIL"] = "1"

    result = _run_deploy(env)

    assert result.returncode == 0, result.stderr
    assert "Could not list historical Agent images" in result.stderr
    calls = calls_log.read_text().splitlines()
    assert any("docker image ls" in call for call in calls)
    assert not any("docker image rm" in call for call in calls)
