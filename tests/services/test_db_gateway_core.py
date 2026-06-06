"""DB gateway core behavior."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.integrations.postgres import PsqlResult, TeleportAuthExpired


def _cfg(tmp_path):
    cfg = MagicMock()
    cfg.postgres.host = "localhost"
    cfg.postgres.port = 56061
    cfg.postgres.user = "read"
    cfg.postgres.password = "pw"
    cfg.postgres.db_prod = "langgenius"
    cfg.postgres.db_billing = "billing"
    cfg.postgres.db_plugin = "dify_plugin_daemon"
    cfg.postgres.port_prod = 56061
    cfg.postgres.port_billing = 56062
    cfg.postgres.port_plugin = 56063
    cfg.postgres.query_timeout_seconds = 30
    cfg.postgres.max_rows = 1000
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    return cfg


@pytest.mark.asyncio
async def test_gateway_query_success(tmp_path):
    from zd_agent.services.db_gateway_core import execute_gateway_query

    fake_result = PsqlResult(
        csv="id,name\n1,alice\n",
        row_count=1,
        elapsed_ms=50,
        truncated=False,
    )
    with patch(
        "zd_agent.services.db_gateway_core.run_psql",
        AsyncMock(return_value=fake_result),
    ):
        result = await execute_gateway_query(
            cfg=_cfg(tmp_path),
            database="prod",
            sql="SELECT id, name FROM tenants LIMIT 1",
            ticket_id="12345",
        )

    assert result.success is True
    assert result.csv == "id,name\n1,alice\n"
    assert result.row_count == 1


@pytest.mark.asyncio
async def test_gateway_rejects_write_without_psql(tmp_path):
    from zd_agent.services.db_gateway_core import execute_gateway_query

    run = AsyncMock()
    with patch("zd_agent.services.db_gateway_core.run_psql", run):
        result = await execute_gateway_query(
            cfg=_cfg(tmp_path),
            database="prod",
            sql="DELETE FROM tenants WHERE id=1",
            ticket_id="12345",
        )

    assert result.success is False
    assert result.code == "SQL_SAFETY_ERROR"
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_teleport_expired_has_explicit_code(tmp_path):
    from zd_agent.services.db_gateway_core import execute_gateway_query

    with patch(
        "zd_agent.services.db_gateway_core.run_psql",
        AsyncMock(side_effect=TeleportAuthExpired("conn refused")),
    ):
        result = await execute_gateway_query(
            cfg=_cfg(tmp_path),
            database="prod",
            sql="SELECT 1",
            ticket_id="12345",
        )

    assert result.success is False
    assert result.code == "TELEPORT_EXPIRED"
    assert "conn refused" in (result.error or "")
