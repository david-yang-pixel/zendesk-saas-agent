import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import InspectorConfig
from zd_agent.tools.inspector_exec import InspectorExec, _terminate_process_tree


_CONFLICTING_BILLING_OUTPUT = """
TENANT
────────
+---------+----------+
| FIELD   | VALUE    |
+---------+----------+
| Plan    | basic    |
+---------+----------+

BILLING
─────────
+---------------------+------------+
| FIELD               | VALUE      |
+---------------------+------------+
| Current Plan        | Team 版    |
| Plan Code           | team       |
| Subscription Status | active     |
| Source              | billing_db |
+---------------------+------------+
""".strip()

_EMAIL_TENANTS_OUTPUT = """
ACCOUNT
---------
| FIELD | VALUE            |
| Email | form@example.com |

TENANTS (1)
-------------
| TENANT ID | NAME      | PLAN  | STATUS | ROLE  | JOINED     |
| tenant-1  | Workspace | basic | normal | owner | 2026-01-01 |
""".strip()


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

    result = await InspectorExec().execute(
        {"email": ["alice@example.com"]}, fake_session
    )

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


def test_default_retry_budget_is_bounded() -> None:
    timeout = InspectorConfig().timeout_seconds

    assert timeout == 45


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

    monkeypatch.setattr(
        "zd_agent.tools.inspector_exec.asyncio.wait_for", timeout_immediately
    )
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)

    result = await InspectorExec().execute(
        {"tenant_id": "tenant-1", "section": "all"},
        fake_session,
    )

    assert result.success is False
    assert spawn.await_count == 2
    assert all(
        call.kwargs["start_new_session"] is True for call in spawn.await_args_list
    )
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


@pytest.mark.asyncio
async def test_billing_section_promotes_billing_db_over_prod_plan(
    fake_session,
    inspector_config,
    monkeypatch,
) -> None:
    class CompletedProcess:
        pid = 4321
        returncode = 0

        async def communicate(self):
            return _CONFLICTING_BILLING_OUTPUT.encode(), b""

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=CompletedProcess()),
    )

    result = await InspectorExec().execute(
        {"tenant_id": "tenant-1", "section": "billing"},
        fake_session,
    )

    assert result.success is True
    assert result.output.startswith(
        "AUTHORITATIVE_BILLING_PLAN=team\n"
        "AUTHORITATIVE_BILLING_STATUS=active\n"
        "AUTHORITATIVE_BILLING_SOURCE=billing_db\n"
    )
    assert "TENANT/TENANTS Plan is non-authoritative" in result.output


@pytest.mark.asyncio
async def test_all_section_cannot_authorize_a_subscription_plan_claim(
    fake_session,
    inspector_config,
    monkeypatch,
) -> None:
    class CompletedProcess:
        pid = 4321
        returncode = 0

        async def communicate(self):
            return _CONFLICTING_BILLING_OUTPUT.encode(), b""

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=CompletedProcess()),
    )

    result = await InspectorExec().execute(
        {"tenant_id": "tenant-1", "section": "all"},
        fake_session,
    )

    assert result.success is True
    assert result.output.startswith("AUTHORITATIVE_BILLING_PLAN=unknown\n")
    assert "rerun inspector_exec with tenant_id + section=billing" in result.output


@pytest.mark.asyncio
async def test_email_tenants_section_emits_machine_readable_workspace_resolution(
    fake_session,
    inspector_config,
    monkeypatch,
) -> None:
    class CompletedProcess:
        pid = 4321
        returncode = 0

        async def communicate(self):
            return _EMAIL_TENANTS_OUTPUT.encode(), b""

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=CompletedProcess()),
    )

    result = await InspectorExec().execute(
        {"email": "form@example.com", "section": "tenants"},
        fake_session,
    )

    assert result.success is True
    assert result.output.startswith(
        "EMAIL_WORKSPACE_RESOLUTION=unique\n"
        "EMAIL_WORKSPACE_COUNT=1\n"
        "EMAIL_WORKSPACE_TENANT_IDS=tenant-1\n"
        'EMAIL_WORKSPACE_CANDIDATES_JSON=[{"tenant_id":"tenant-1",'
        '"name":"Workspace"}]\n'
        "EMAIL_WORKSPACE_SOURCE=prod_membership\n"
    )
    assert "Plan is non-authoritative" in result.output
    assert result.metadata == {
        "workspace_resolution": {
            "source": "prod_membership",
            "status": "unique",
            "count": 1,
            "tenant_ids": ["tenant-1"],
            "candidates": [
                {"tenant_id": "tenant-1", "name": "Workspace"},
            ],
        },
    }
