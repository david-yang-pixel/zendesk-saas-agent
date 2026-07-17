import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import InspectorConfig
from zd_agent.runtime.agent_loop import _TOOL_CALL_TIMEOUT_SECONDS
from zd_agent.tools.inspector_exec import InspectorExec, _terminate_process_tree


@pytest.fixture
def inspector_config(monkeypatch):
    cfg = SimpleNamespace(
        inspector=SimpleNamespace(
            binary="/bin/echo",
            config_file="/tmp/inspector.yaml",
            timeout_seconds=5,
        ),
    )
    monkeypatch.setattr("zd_agent.tools.inspector_exec.get_config", lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_rejects_non_string_primary_query_before_spawn(
    fake_session,
    inspector_config,
    monkeypatch,
):
    spawn = AsyncMock(side_effect=AssertionError("subprocess should not be called"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)

    result = await InspectorExec().execute({"email": ["alice@example.com"]}, fake_session)

    assert result.success is False
    assert result.error == "email must be a string"
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_non_string_date_filter_before_spawn(
    fake_session,
    inspector_config,
    monkeypatch,
):
    spawn = AsyncMock(side_effect=AssertionError("subprocess should not be called"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)

    result = await InspectorExec().execute(
        {"tenant_id": "tenant-1", "from_date": ["2026-01-01"]},
        fake_session,
    )

    assert result.success is False
    assert result.error == "from_date must be a string"
    spawn.assert_not_called()


def test_default_retry_budget_leaves_room_below_outer_tool_timeout() -> None:
    timeout = InspectorConfig().timeout_seconds

    assert timeout == 45
    assert timeout * 2 < _TOOL_CALL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_timeout_kills_inspector_process_group_before_retry(
    fake_session,
    inspector_config,
    monkeypatch,
) -> None:
    inspector_config.inspector.timeout_seconds = 0.01

    class HangingProcess:
        pid = 4321
        returncode = None

        async def communicate(self):
            await asyncio.Event().wait()

        async def wait(self):
            self.returncode = -9
            return self.returncode

        def kill(self) -> None:
            raise AssertionError("process-group kill should be used on POSIX")

    processes = [HangingProcess(), HangingProcess()]
    spawn = AsyncMock(side_effect=processes)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)

    async def timeout_immediately(awaitable, *, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr("zd_agent.tools.inspector_exec.asyncio.wait_for", timeout_immediately)
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)

    result = await InspectorExec().execute(
        {"tenant_id": "tenant-1", "section": "all"},
        fake_session,
    )

    assert result.success is False
    assert spawn.await_count == 2
    assert all(call.kwargs["start_new_session"] is True for call in spawn.await_args_list)
    assert killpg.call_count == 2
    assert [call.args for call in killpg.call_args_list] == [
        (4321, 9),
        (4321, 9),
    ]


@pytest.mark.asyncio
async def test_process_tree_cleanup_reaps_already_exited_child(monkeypatch) -> None:
    class ExitedProcess:
        pid = 4321
        returncode = None

        def __init__(self) -> None:
            self.waited = False

        async def wait(self):
            self.waited = True
            self.returncode = 0
            return 0

    process = ExitedProcess()
    monkeypatch.setattr(os, "killpg", MagicMock(side_effect=ProcessLookupError))

    await _terminate_process_tree(process)

    assert process.waited is True


@pytest.mark.asyncio
async def test_external_cancellation_terminates_inspector_process_group(
    fake_session,
    inspector_config,
    monkeypatch,
) -> None:
    started = asyncio.Event()

    class HangingProcess:
        pid = 4321
        returncode = None

        def __init__(self) -> None:
            self.waited = False

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        async def wait(self):
            self.waited = True
            self.returncode = -9
            return self.returncode

    process = HangingProcess()
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)

    task = asyncio.create_task(
        InspectorExec().execute({"tenant_id": "tenant-1"}, fake_session),
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    killpg.assert_called_once_with(4321, 9)
    assert process.waited is True
