from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from zd_agent.tools.inspector_exec import InspectorExec


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
