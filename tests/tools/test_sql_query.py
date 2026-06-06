"""Test SqlQuery tool."""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from zd_agent.tools.sql_query import SqlQuery
from zd_agent.integrations.postgres import PsqlResult, TeleportAuthExpired


@pytest.fixture
def sql_cfg(tmp_path, monkeypatch):
    """Patch get_config to return a cfg with tmp resources + known postgres settings."""
    cfg = MagicMock()
    cfg.postgres.host = "localhost"
    cfg.postgres.port = 56061
    cfg.postgres.user = "read"
    cfg.postgres.password = "pw"
    cfg.postgres.db_prod = "langgenius"
    cfg.postgres.db_billing = "billing"
    cfg.postgres.db_plugin = "dify_plugin_daemon"
    cfg.postgres.query_timeout_seconds = 30
    cfg.postgres.max_rows = 1000
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    cfg.db_gateway.url = ""
    cfg.db_gateway.api_key = ""
    cfg.db_gateway.request_timeout_seconds = 30

    monkeypatch.setattr("zd_agent.tools.sql_query.get_config", lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_sql_query_rejects_write(fake_session, sql_cfg):
    tool = SqlQuery()
    result = await tool.execute(
        {"database": "prod", "sql": "DELETE FROM tenants WHERE id=1"},
        fake_session,
    )
    assert result.success is False
    assert "DELETE" in (result.error or "").upper() or "forbidden" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_sql_query_rejects_unknown_database(fake_session, sql_cfg):
    tool = SqlQuery()
    result = await tool.execute(
        {"database": "unknown_db", "sql": "SELECT 1"},
        fake_session,
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_sql_query_success(fake_session, sql_cfg):
    tool = SqlQuery()
    fake_result = PsqlResult(csv="id,name\n1,alice\n", row_count=1, elapsed_ms=50, truncated=False)
    with patch("zd_agent.tools.sql_query.run_psql", AsyncMock(return_value=fake_result)):
        result = await tool.execute(
            {"database": "prod", "sql": "SELECT id, name FROM tenants LIMIT 1"},
            fake_session,
        )
    assert result.success is True
    assert "alice" in result.output


@pytest.mark.asyncio
async def test_sql_query_teleport_expired_marks_session(fake_session, sql_cfg):
    tool = SqlQuery()
    with patch(
        "zd_agent.tools.sql_query.run_psql",
        AsyncMock(side_effect=TeleportAuthExpired("conn refused")),
    ):
        result = await tool.execute(
            {"database": "prod", "sql": "SELECT 1"},
            fake_session,
        )
    assert result.success is False
    assert "teleport" in (result.error or "").lower() or "认证" in (result.error or "")
    assert fake_session.waiting_for_auth is True  # session marked


@pytest.mark.asyncio
async def test_sql_query_writes_audit_log(fake_session, sql_cfg, tmp_path):
    tool = SqlQuery()
    fake_result = PsqlResult(csv="id\n1\n", row_count=1, elapsed_ms=10, truncated=False)
    with patch("zd_agent.tools.sql_query.run_psql", AsyncMock(return_value=fake_result)):
        await tool.execute(
            {"database": "prod", "sql": "SELECT id FROM tenants LIMIT 1"},
            fake_session,
        )
    audit_log = tmp_path / ".state" / "logs" / "sql_audit.log"
    assert audit_log.exists()
    content = audit_log.read_text()
    assert "SELECT id FROM tenants LIMIT 1" in content
    assert '"status": "OK"' in content


@pytest.mark.asyncio
async def test_sql_query_uses_db_gateway_mcp_when_configured(fake_session, sql_cfg):
    from zd_agent.integrations.db_gateway import DbGatewayResult

    sql_cfg.db_gateway.url = "https://db-gateway.example.com"
    sql_cfg.db_gateway.api_key = "secret"
    sql_cfg.db_gateway.mcp_enabled = True
    sql_cfg.db_gateway.mcp_binary = "/tmp/db-gateway-mcp"
    tool = SqlQuery()
    gateway_result = DbGatewayResult(
        success=True,
        csv="id,name\n1,alice\n",
        row_count=1,
        elapsed_ms=42,
        truncated=False,
    )
    with patch(
        "zd_agent.tools.sql_query.query_db_gateway_mcp",
        AsyncMock(return_value=gateway_result),
    ) as gateway_mcp, patch(
        "zd_agent.tools.sql_query.query_db_gateway",
        AsyncMock(),
    ) as gateway_http, patch(
        "zd_agent.tools.sql_query.run_psql",
        AsyncMock(),
    ) as direct:
        result = await tool.execute(
            {"database": "prod", "sql": "SELECT id, name FROM tenants LIMIT 1"},
            fake_session,
        )

    assert result.success is True
    assert "alice" in result.output
    gateway_mcp.assert_awaited_once()
    gateway_http.assert_not_awaited()
    direct.assert_not_awaited()
