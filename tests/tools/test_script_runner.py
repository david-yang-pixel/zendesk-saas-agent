from pathlib import Path
from unittest.mock import MagicMock

import pytest

import zd_agent.tools.script_runner as sr
from zd_agent.tools.script_runner import RunScript, SandboxExec


@pytest.fixture
def sandbox_session(tmp_path: Path):
    session = MagicMock()
    session.sandbox_dir = tmp_path
    return session


@pytest.fixture
def script_dir(tmp_path: Path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "check-plugin-runtime.sh"
    script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    monkeypatch.setattr(sr, "SCRIPTS_DIR", scripts)
    return scripts


@pytest.mark.asyncio
async def test_run_script_rejects_missing_or_blank_script_name(sandbox_session, script_dir):
    result = await RunScript().execute({}, sandbox_session)

    assert result.success is False
    assert result.error == "script_name is required"

    result = await RunScript().execute({"script_name": "   "}, sandbox_session)

    assert result.success is False
    assert result.error == "script_name is empty"


@pytest.mark.asyncio
async def test_run_script_args_must_be_string_list(sandbox_session, script_dir):
    result = await RunScript().execute(
        {"script_name": "check-plugin-runtime.sh", "args": "--bad"},
        sandbox_session,
    )

    assert result.success is False
    assert "args" in (result.error or "")

    result = await RunScript().execute(
        {"script_name": "check-plugin-runtime.sh", "args": ["ok", 123]},
        sandbox_session,
    )

    assert result.success is False
    assert "args" in (result.error or "")


@pytest.mark.asyncio
async def test_run_script_executes_allowed_script(sandbox_session, script_dir):
    result = await RunScript().execute(
        {"script_name": "check-plugin-runtime.sh", "args": ["one"]},
        sandbox_session,
    )

    assert result.success is True
    assert "ok" in (result.output or "")


@pytest.mark.asyncio
async def test_sandbox_exec_command_and_cwd_must_be_strings(sandbox_session):
    result = await SandboxExec().execute({}, sandbox_session)

    assert result.success is False
    assert result.error == "command is required"

    result = await SandboxExec().execute({"command": 123}, sandbox_session)

    assert result.success is False
    assert "command" in (result.error or "")

    result = await SandboxExec().execute({"command": "   "}, sandbox_session)

    assert result.success is False
    assert result.error == "command is empty"

    result = await SandboxExec().execute(
        {"command": "pwd", "cwd": ["src"]},
        sandbox_session,
    )

    assert result.success is False
    assert "cwd" in (result.error or "")


@pytest.mark.asyncio
async def test_sandbox_exec_runs_allowed_command(sandbox_session):
    result = await SandboxExec().execute({"command": "pwd"}, sandbox_session)

    assert result.success is True
    assert "退出码: 0" in (result.output or "")
