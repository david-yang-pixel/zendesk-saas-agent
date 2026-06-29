from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.tools.script_runner import RunScript, SandboxExec


@pytest.fixture
def sandbox_session(tmp_path: Path):
    session = MagicMock()
    session.sandbox_dir = tmp_path
    return session


@pytest.mark.asyncio
async def test_run_script_rejects_missing_or_blank_script_name(sandbox_session):
    result = await RunScript().execute({}, sandbox_session)

    assert result.success is False
    assert result.error == "script_name is required"

    result = await RunScript().execute({"script_name": "   "}, sandbox_session)

    assert result.success is False
    assert result.error == "script_name is empty"


@pytest.mark.asyncio
async def test_run_script_args_must_be_string_list(sandbox_session):
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
async def test_run_script_executes_allowed_script(sandbox_session):
    with patch(
        "zd_agent.tools.script_runner.call_sandbox_worker_tool",
        new_callable=AsyncMock,
        return_value={"success": True, "output": "退出码: 0\n\nok\n"},
    ) as worker:
        result = await RunScript().execute(
            {"script_name": "check-plugin-runtime.sh", "args": ["one"]},
            sandbox_session,
        )

    assert result.success is True
    assert "ok" in (result.output or "")
    worker.assert_awaited_once()
    assert worker.await_args.args[0] == "run_script"


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
    with patch(
        "zd_agent.tools.script_runner.call_sandbox_worker_tool",
        new_callable=AsyncMock,
        return_value={"success": True, "output": "退出码: 0\n/tmp/sandbox\n"},
    ) as worker:
        result = await SandboxExec().execute({"command": "pwd"}, sandbox_session)

    assert result.success is True
    assert "退出码: 0" in (result.output or "")
    worker.assert_awaited_once()
    assert worker.await_args.args[0] == "sandbox_exec"


def test_sandbox_exec_schema_warns_about_single_line_commands():
    schema = SandboxExec().to_openai_schema()
    command_desc = schema["function"]["parameters"]["properties"]["command"]["description"]

    assert "单行" in command_desc
    assert "换行" in command_desc


@pytest.mark.asyncio
async def test_sandbox_exec_rejects_shell_control_operators(sandbox_session):
    result = await SandboxExec().execute(
        {"command": "echo safe; echo unsafe"},
        sandbox_session,
    )

    assert result.success is False
    assert "shell control operator" in (result.error or "")
