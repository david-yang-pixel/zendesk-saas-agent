from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from deploy import prepare_runtime

ROOT = Path(__file__).resolve().parents[3]
CURRENT = "a" * 40
TARGET = "b" * 40
SERVICES = ("ingress", "admin", "pi-agent")
COMMAND = r'''#!/usr/bin/env python3
import os
import sys
from pathlib import Path

command = Path(sys.argv[0]).name
args = sys.argv[1:]
state = Path(os.environ["CASEWORK_STATE_DIR"])
log = state / "commands"

def record(*values):
    with log.open("a") as file:
        print(*values, file=file)

if command == "docker":
    verb = args[0]
    if verb == "ps":
        service = next(arg for arg in args if arg.startswith("label=com.docker.compose.service=")).split("=")[-1]
        filename = state / f"running-{service}"
        if filename.exists() or (service == "postgres" and (state / "stopped-postgres").exists() and "-a" in args):
            print(service)
    elif verb == "exec":
        sql = args[args.index("-c") + 1].strip() if "-c" in args else sys.stdin.read()
        if sql == "SELECT version_num FROM public.alembic_version":
            print('0015_handoff_resume')
        elif sql == "SELECT 1":
            print(1)
        elif "to_regclass('public.case_jobs')" in sql:
            print("1|t|" + ("t" if (state / "fenced").exists() else "f"))
        elif "enable_fence=true" in args:
            record("fence enabled")
            (state / "fenced").touch()
        elif "enable_fence=false" in args:
            record("fence cleared")
            (state / "fenced").unlink(missing_ok=True)
        elif sql.startswith("SELECT EXISTS"):
            assert (state / "fenced").exists()
            assert not (state / "running-ingress").exists()
            assert not (state / "running-admin").exists()
            record("check active")
            print("t" if os.environ.get("ACTIVE_JOBS") else "f")
        elif "Rollback blocked" in sql:
            record("check rollback")
            assert (state / "fenced").exists()
            assert not (state / "running-pi-agent").exists()
            if reason := os.environ.get("ROLLBACK_BLOCKED"):
                print(f"Rollback blocked: {reason}", file=sys.stderr)
                sys.exit(1)
        elif "casework_deploy_legacy_queued_ticket" in sql:
            assert not (state / "running-pi-agent").exists()
            record("set queue layout", args[-1])
        else:
            raise AssertionError(sql)
    elif verb == "stop":
        record(verb, *args[3:])
        for service in args[3:]:
            (state / f"running-{service}").unlink()
    elif verb == "start":
        record(verb, *args[1:])
        for service in args[1:]:
            (state / f"running-{service}").touch()
    elif verb == "run" and "--entrypoint" in args:
        print(os.environ.get("TARGET_SCHEMA_HEAD", "0015_handoff_resume"))
    elif verb == "compose":
        action = next(arg for arg in args if arg in ("up", "run", "config"))
        command_args = args[args.index(action):]
        record(*command_args)
        if action == "run":
            assert not (state / "running-pi-agent").exists()
            if os.environ.get("MIGRATION_FAILS"):
                sys.exit(17)
        if action == "up":
            for service in command_args:
                if service in ("postgres", "ingress", "admin", "pi-agent", "log-sync"):
                    (state / f"running-{service}").touch()
    else:
        record(verb, *args[1:])
elif command == "python3":
    record(Path(args[0]).name, *args[1:])
    if args[0].endswith("prepare_runtime.py"):
        (state / "runtime.env").write_text('CASEWORK_LOG_SYNC_ENABLED="true"\n')
else:
    record(command, *args)
'''


class ReleaseHarness:
    def __init__(self, tmp_path: Path) -> None:
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.tools = tmp_path / "bin"
        self.tools.mkdir()
        command = self.tools / "command"
        command.write_text(COMMAND.replace("/usr/bin/env python3", sys.executable))
        command.chmod(0o755)
        for tool in ("docker", "curl", "python3", "nginx", "systemctl"):
            (self.tools / tool).symlink_to(command)
        self.current_release = self.state / "release.env"
        self.previous_release = self.state / "previous-release.env"
        self.runtime = self.state / "runtime.env"

    def existing_release(self) -> None:
        for service in (*SERVICES, "postgres", "log-sync"):
            (self.state / f"running-{service}").touch()
        self.current_release.write_text(f"CASEWORK_RELEASE_REVISION={CURRENT}\n")
        self.previous_release.write_text(
            f"CASEWORK_RELEASE_REVISION={TARGET}\n"
            + "".join(
                f"CASEWORK_{name}_IMAGE=ghcr.io/target@sha256:{'b' * 64}\n"
                for name in ("INGRESS", "ADMIN", "PI_AGENT")
            )
        )
        self.runtime.write_text("original runtime\n")

    def run(self, script: str = "deploy", **overrides: str) -> subprocess.CompletedProcess[str]:
        entrypoint = ROOT / f"deploy/{script}-production.sh"
        # The harness runs with fake Docker and a private state directory. Skip
        # the root check so the same ordering tests run under an ordinary user.
        body = entrypoint.read_text().replace('if [[ "$EUID" -ne 0 ]]; then', "if false; then", 1)
        return subprocess.run(
            ["bash", "-c", body],
            env={
                **os.environ,
                "PATH": f"{self.tools}{os.pathsep}{os.environ['PATH']}",
                "ZD_AGENT_CODE_DIR": str(ROOT),
                "ZD_AGENT_DEPLOY_LOCK_FILE": str(self.state / "lock"),
                "CASEWORK_STATE_DIR": str(self.state),
                "CASEWORK_NGINX_CONFIG": str(self.state / "nginx.conf"),
                "CASEWORK_DEPLOY_DRAIN_TIMEOUT": "1",
                "GHCR_USERNAME": "user",
                "GHCR_PAT": "token",
                "CASEWORK_RELEASE_REVISION": TARGET,
                **{f"CASEWORK_{name}_IMAGE": f"ghcr.io/target@sha256:{'b' * 64}"
                   for name in ("INGRESS", "ADMIN", "PI_AGENT")},
                **overrides,
            },
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def assert_order(self, *commands: str) -> None:
        lines = (self.state / "commands").read_text().splitlines()
        offset = 0
        for expected in commands:
            for index in range(offset, len(lines)):
                if lines[index].startswith(expected):
                    offset = index + 1
                    break
            else:
                pytest.fail(f"Expected {expected!r} after line {offset} in {lines!r}")

    def commands(self) -> str:
        return (self.state / "commands").read_text()


def test_deploy_fences_old_worker_before_migrating(tmp_path: Path) -> None:
    harness = ReleaseHarness(tmp_path)
    harness.existing_release()
    result = harness.run()
    assert result.returncode == 0, result.stderr
    harness.assert_order(
        "pull ghcr.io/target",
        "fence enabled",
        "stop ingress admin log-sync",
        "check active",
        "stop pi-agent",
        "run --rm --no-deps migration",
        "set queue layout legacy_queue=false",
        "up -d --no-deps ingress admin log-sync",
        "configure_runtime.py",
        "up -d --no-deps pi-agent",
        "curl -fsS --max-time 3 http://127.0.0.1:18102/readyz",
        "fence cleared",
    )
    assert CURRENT in harness.previous_release.read_text()
    assert TARGET in harness.current_release.read_text()


def test_initial_deploy_starts_postgres_then_runs_explicit_migration(tmp_path: Path) -> None:
    harness = ReleaseHarness(tmp_path)
    result = harness.run()
    assert result.returncode == 0, result.stderr
    harness.assert_order(
        "up -d --wait --wait-timeout 120 postgres",
        "run --rm --no-deps migration",
        "set queue layout legacy_queue=false",
        "up -d --no-deps ingress admin log-sync",
        "up -d --no-deps pi-agent",
    )
    assert "fence enabled" not in harness.commands()


def test_drain_timeout_resumes_existing_release_without_stopping_worker(tmp_path: Path) -> None:
    harness = ReleaseHarness(tmp_path)
    harness.existing_release()
    prior = harness.previous_release.read_text()
    result = harness.run(ACTIVE_JOBS="yes")
    assert result.returncode != 0
    assert "did not drain" in result.stderr
    harness.assert_order("fence enabled", "stop ingress admin log-sync", "check active", "start ingress", "fence cleared")
    assert "stop pi-agent" not in harness.commands()
    assert "run --rm" not in harness.commands()
    assert (harness.state / "running-pi-agent").exists()
    assert harness.runtime.read_text() == "original runtime\n"
    assert harness.previous_release.read_text() == prior


def test_migration_failure_keeps_previous_worker_stopped_and_fenced(tmp_path: Path) -> None:
    harness = ReleaseHarness(tmp_path)
    harness.existing_release()
    prior = harness.previous_release.read_text()
    result = harness.run(MIGRATION_FAILS="yes")
    assert result.returncode == 17, result.stderr
    harness.assert_order("stop pi-agent", "run --rm --no-deps migration")
    assert "up -d --no-deps pi-agent" not in harness.commands()
    assert "start pi-agent" not in harness.commands()
    assert not (harness.state / "running-pi-agent").exists()
    assert (harness.state / "fenced").exists()
    assert CURRENT in harness.current_release.read_text()
    assert harness.previous_release.read_text() == prior

    # A forward retry can complete using the durable fence and retains the
    # last healthy release as its rollback target.
    result = harness.run()
    assert result.returncode == 0, result.stderr
    harness.assert_order(
        "run --rm --no-deps migration",
        "fence enabled",
        "run --rm --no-deps migration",
        "up -d --no-deps pi-agent",
        "fence cleared",
    )
    assert CURRENT in harness.previous_release.read_text()


@pytest.mark.parametrize("reason", ["incompatible queue", "unresolved public deliveries"])
def test_rollback_guard_refusal_restores_running_release(tmp_path: Path, reason: str) -> None:
    harness = ReleaseHarness(tmp_path)
    harness.existing_release()
    result = harness.run("rollback", ROLLBACK_BLOCKED=reason)
    assert result.returncode != 0
    assert reason in result.stderr
    harness.assert_order("stop pi-agent", "check rollback", "start ingress admin log-sync pi-agent", "fence cleared")
    assert "prepare_runtime.py" not in harness.commands()
    assert "up -d" not in harness.commands()
    assert harness.runtime.read_text() == "original runtime\n"


def test_incompatible_schema_blocks_before_stopping_services(tmp_path: Path) -> None:
    harness = ReleaseHarness(tmp_path)
    harness.existing_release()
    result = harness.run("rollback", TARGET_SCHEMA_HEAD="0011_human_handoffs")
    assert result.returncode != 0
    assert "target image requires 0011_human_handoffs" in result.stderr
    assert all((harness.state / f"running-{service}").exists() for service in SERVICES)
    assert harness.runtime.read_text() == "original runtime\n"


def test_successful_rollback_skips_migration_and_retains_fence_until_ready(tmp_path: Path) -> None:
    harness = ReleaseHarness(tmp_path)
    harness.existing_release()
    result = harness.run("rollback")
    assert result.returncode == 0, result.stderr
    harness.assert_order(
        "stop pi-agent",
        "check rollback",
        "set queue layout legacy_queue=true",
        "prepare_runtime.py",
        "up -d --no-deps ingress admin log-sync",
        "up -d --no-deps pi-agent",
        "curl -fsS --max-time 5 https://zendesk.smlershou.top/healthz",
        "fence cleared",
    )
    assert "--images-only" in harness.commands()
    assert "run --rm" not in harness.commands()
    assert TARGET in harness.current_release.read_text()
    assert CURRENT in harness.previous_release.read_text()


def test_rollback_to_operator_mention_layout_uses_current_queue_index(tmp_path: Path) -> None:
    harness = ReleaseHarness(tmp_path)
    harness.existing_release()
    with harness.previous_release.open("a") as file:
        file.write("CASEWORK_RELEASE_QUEUE_LAYOUT=operator_mentions\n")
    result = harness.run("rollback")
    assert result.returncode == 0, result.stderr
    harness.assert_order("check rollback", "set queue layout legacy_queue=false", "up -d --no-deps pi-agent")


def test_rollback_runtime_updates_only_release_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "runtime.env"
    runtime.write_text('CASEWORK_LOG_SYNC_ENABLED="true"\nCASEWORK_TOOL_CONFIG="saved"\n')
    for role in ("INGRESS", "ADMIN", "PI_AGENT"):
        monkeypatch.setenv(f"CASEWORK_{role}_IMAGE", f"image-{role}")
    prepare_runtime.prepare(state_dir=tmp_path, revision=TARGET, images_only=True)
    assert prepare_runtime._parse_dotenv(runtime) == {  # pyright: ignore[reportPrivateUsage]
        "CASEWORK_LOG_SYNC_ENABLED": "true",
        "CASEWORK_TOOL_CONFIG": "saved",
        "CASEWORK_CONFIG_REVISION": TARGET,
        "CASEWORK_INGRESS_IMAGE": "image-INGRESS",
        "CASEWORK_ADMIN_IMAGE": "image-ADMIN",
        "CASEWORK_PI_AGENT_IMAGE": "image-PI_AGENT",
    }
