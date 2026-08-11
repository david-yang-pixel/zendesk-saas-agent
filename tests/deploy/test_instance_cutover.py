from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "demote-instance.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _environment(tmp_path: Path, *, container_running: bool = False):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls"
    _write_executable(
        fake_bin / "flock",
        '#!/usr/bin/env bash\nset -e\nprintf \'flock %s\\n\' "$*" >>"$CALLS"\n',
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
set -e
printf 'systemctl %s\n' "$*" >>"$CALLS"
case "${1:-}" in
  show) printf 'inactive\n' ;;
  is-enabled) printf 'masked\n' ;;
esac
""",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -e
printf 'docker %s\n' "$*" >>"$CALLS"
if [[ "${1:-}" == ps && "${CONTAINER_RUNNING:-0}" == 1 ]]; then
  printf 'still-running\n'
fi
""",
    )
    resources = tmp_path / "resources"
    state = resources / ".state"
    state.mkdir(parents=True)
    (state / "active_instance_id").write_text("old-host\n")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CALLS": str(calls),
            "CONTAINER_RUNNING": "1" if container_running else "0",
            "ZD_AGENT_RESOURCES_DIR": str(resources),
            "ZD_AGENT_INSTANCE_ID": "old-host",
            "ZD_AGENT_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
        },
    )
    return env, state, calls


def test_demote_stops_masks_and_transfers_instance_marker(tmp_path: Path) -> None:
    env, state, calls = _environment(tmp_path)

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT), "new-host"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (state / "active_instance_id").read_text() == "new-host\n"
    logged = calls.read_text()
    assert "systemctl stop zd-agent" in logged
    assert "systemctl mask --force zd-agent" in logged
    assert "docker ps" in logged


def test_demote_never_transfers_marker_while_old_container_runs(
    tmp_path: Path,
) -> None:
    env, state, _calls = _environment(tmp_path, container_running=True)

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT), "new-host"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert (state / "active_instance_id").read_text() == "old-host\n"


def test_demote_refuses_a_stale_host_that_does_not_own_the_marker(
    tmp_path: Path,
) -> None:
    env, state, calls = _environment(tmp_path)
    (state / "active_instance_id").write_text("another-host\n")

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT), "new-host"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "does not own" in result.stderr
    assert (state / "active_instance_id").read_text() == "another-host\n"
    assert not calls.exists()
