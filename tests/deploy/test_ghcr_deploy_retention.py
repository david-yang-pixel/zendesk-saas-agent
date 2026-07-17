from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "deploy" / "deploy-ghcr.sh"
SYSTEM_BASH = Path("/bin/bash")


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
  pull|tag|login)
    exit 0
    ;;
  inspect)
    printf '%s\n' 'sha256:1111111111111111111111111111111111111111111111111111111111111111'
    ;;
  image)
    case "${2:-}" in
      inspect)
        ref="${3:-}"
        if [[ "$*" == *'{{.Created}}'* ]]; then
          case "$ref" in
            *:sha-current|*:main)
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
        else
          printf '%s\n' 'sha256:1111111111111111111111111111111111111111111111111111111111111111'
        fi
        ;;
      ls)
        if [[ "${FAKE_IMAGE_LS_FAIL:-0}" == "1" ]]; then
          exit 1
        fi
        cat <<'EOF'
ghcr.io/example/zendesk-saas-agent:sha-old sha256:3333333333333333333333333333333333333333333333333333333333333333
ghcr.io/example/zendesk-saas-agent:main sha256:1111111111111111111111111111111111111111111111111111111111111111
ghcr.io/example/unrelated:latest sha256:4444444444444444444444444444444444444444444444444444444444444444
ghcr.io/example/zendesk-saas-agent:sha-previous sha256:2222222222222222222222222222222222222222222222222222222222222222
ghcr.io/example/zendesk-saas-agent:sha-current sha256:1111111111111111111111111111111111111111111111111111111111111111
EOF
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
  *)
    printf 'unexpected docker command: %s\n' "${1:-}" >&2
    exit 64
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl' >> "$FAKE_CALLS_LOG"
printf ' %q' "$@" >> "$FAKE_CALLS_LOG"
printf '\n' >> "$FAKE_CALLS_LOG"
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
if [[ "${FAKE_HEALTH_FAIL:-0}" == "1" ]]; then
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
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_CALLS_LOG": str(calls_log),
            "ZD_AGENT_IMAGE": "ghcr.io/example/zendesk-saas-agent",
            "ZD_AGENT_IMAGE_KEEP_RECENT": "2",
            "ZD_AGENT_HEALTH_TIMEOUT_SECONDS": "1",
        }
    )
    return env, calls_log


def test_successful_deploy_prunes_only_unprotected_historical_agent_images(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)

    result = subprocess.run(
        [str(SYSTEM_BASH), str(DEPLOY_SCRIPT), "sha-current"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = calls_log.read_text().splitlines()
    assert (
        "docker image rm ghcr.io/example/zendesk-saas-agent:sha-old" in calls
    )
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


def test_failed_health_check_never_prunes_images(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_HEALTH_FAIL"] = "1"

    result = subprocess.run(
        [str(SYSTEM_BASH), str(DEPLOY_SCRIPT), "sha-current"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    calls = calls_log.read_text().splitlines()
    assert any(call.startswith("docker pull ") for call in calls)
    assert any(call.startswith("systemctl restart ") for call in calls)
    assert not any("docker image rm" in call for call in calls)
    assert not any("docker image prune" in call for call in calls)
    assert not any("docker system prune" in call for call in calls)


def test_invalid_retention_count_fails_before_deployment(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["ZD_AGENT_IMAGE_KEEP_RECENT"] = "0"

    result = subprocess.run(
        [str(SYSTEM_BASH), str(DEPLOY_SCRIPT), "sha-current"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ZD_AGENT_IMAGE_KEEP_RECENT must be a positive integer" in result.stderr
    assert not calls_log.exists() or calls_log.read_text() == ""


def test_failed_freshness_check_never_prunes_images(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_FRESHNESS_STATUS"] = "500"

    result = subprocess.run(
        [str(SYSTEM_BASH), str(DEPLOY_SCRIPT), "sha-current"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    calls = calls_log.read_text().splitlines()
    assert any("/webhook/relay-pre-send-freshness" in call for call in calls)
    assert not any("docker image ls" in call for call in calls)
    assert not any("docker image rm" in call for call in calls)


def test_cleanup_failure_warns_without_failing_healthy_deploy(tmp_path: Path) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_IMAGE_RM_FAIL"] = "1"

    result = subprocess.run(
        [str(SYSTEM_BASH), str(DEPLOY_SCRIPT), "sha-current"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Could not remove historical image" in result.stderr
    calls = calls_log.read_text().splitlines()
    assert (
        "docker image rm ghcr.io/example/zendesk-saas-agent:sha-old" in calls
    )


def test_image_list_failure_warns_without_failing_healthy_deploy(
    tmp_path: Path,
) -> None:
    env, calls_log = _fake_command_environment(tmp_path)
    env["FAKE_IMAGE_LS_FAIL"] = "1"

    result = subprocess.run(
        [str(SYSTEM_BASH), str(DEPLOY_SCRIPT), "sha-current"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Could not list historical Agent images" in result.stderr
    calls = calls_log.read_text().splitlines()
    assert any("docker image ls" in call for call in calls)
    assert not any("docker image rm" in call for call in calls)
