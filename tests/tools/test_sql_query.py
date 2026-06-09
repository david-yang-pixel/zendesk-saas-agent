"""Test SqlQuery tool."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.integrations.db_gateway import DbGatewayResult
from zd_agent.tools.sql_query import SqlQuery


@pytest.fixture
def sql_cfg(tmp_path, monkeypatch):
    """Patch get_config to return a cfg with known DB Gateway settings."""
    cfg = MagicMock()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    cfg.db_gateway.url = "https://db-gateway.example.com"
    cfg.db_gateway.api_key = "secret"
    cfg.db_gateway.mcp_enabled = True
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
async def test_sql_query_allows_forbidden_words_inside_string_literals(fake_session, sql_cfg):
    tool = SqlQuery()
    fake_result = DbGatewayResult(
        success=True,
        csv="msg\nplease update your app\n",
        row_count=1,
        elapsed_ms=20,
        truncated=False,
    )
    with patch(
        "zd_agent.tools.sql_query.query_db_gateway_mcp",
        AsyncMock(return_value=fake_result),
    ):
        result = await tool.execute(
            {
                "database": "prod",
                "sql": "SELECT 'please update your app' AS msg",
            },
            fake_session,
        )

    assert result.success is True
    assert "please update your app" in result.output


@pytest.mark.asyncio
async def test_sql_query_allows_forbidden_words_inside_escaped_string(fake_session, sql_cfg):
    tool = SqlQuery()
    fake_result = DbGatewayResult(
        success=True,
        csv="msg\nuser asked: drop it\n",
        row_count=1,
        elapsed_ms=20,
        truncated=False,
    )
    with patch(
        "zd_agent.tools.sql_query.query_db_gateway_mcp",
        AsyncMock(return_value=fake_result),
    ):
        result = await tool.execute(
            {
                "database": "prod",
                "sql": "SELECT 'user asked: ''drop it''' AS msg",
            },
            fake_session,
        )

    assert result.success is True
    assert "drop it" in result.output


@pytest.mark.asyncio
async def test_sql_query_allows_forbidden_words_inside_comments(fake_session, sql_cfg):
    tool = SqlQuery()
    fake_result = DbGatewayResult(
        success=True,
        csv="ok\n1\n",
        row_count=1,
        elapsed_ms=20,
        truncated=False,
    )
    with patch(
        "zd_agent.tools.sql_query.query_db_gateway_mcp",
        AsyncMock(return_value=fake_result),
    ):
        result = await tool.execute(
            {
                "database": "prod",
                "sql": "/* drop old note */ SELECT 1 AS ok -- update comment",
            },
            fake_session,
        )

    assert result.success is True
    assert "Rows: 1" in result.output


@pytest.mark.asyncio
async def test_sql_query_allows_forbidden_words_inside_dollar_quoted_strings(
    fake_session,
    sql_cfg,
):
    tool = SqlQuery()
    fake_result = DbGatewayResult(
        success=True,
        csv="msg\nplease update your app\n",
        row_count=1,
        elapsed_ms=20,
        truncated=False,
    )
    with patch(
        "zd_agent.tools.sql_query.query_db_gateway_mcp",
        AsyncMock(return_value=fake_result),
    ):
        result = await tool.execute(
            {
                "database": "prod",
                "sql": "SELECT $support$please update your app$support$ AS msg",
            },
            fake_session,
        )

    assert result.success is True
    assert "please update your app" in result.output


@pytest.mark.asyncio
async def test_sql_query_allows_forbidden_words_inside_quoted_identifiers(
    fake_session,
    sql_cfg,
):
    tool = SqlQuery()
    fake_result = DbGatewayResult(
        success=True,
        csv="drop\n1\n",
        row_count=1,
        elapsed_ms=20,
        truncated=False,
    )
    with patch(
        "zd_agent.tools.sql_query.query_db_gateway_mcp",
        AsyncMock(return_value=fake_result),
    ):
        result = await tool.execute(
            {
                "database": "prod",
                "sql": 'SELECT "drop" FROM audit_logs LIMIT 1',
            },
            fake_session,
        )

    assert result.success is True
    assert "Rows: 1" in result.output


@pytest.mark.asyncio
async def test_sql_query_rejects_unknown_database(fake_session, sql_cfg):
    tool = SqlQuery()
    result = await tool.execute(
        {"database": "unknown_db", "sql": "SELECT 1"},
        fake_session,
    )
    assert result.success is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "expected_error"),
    [
        ({"database": ["prod"], "sql": "SELECT 1"}, "database must be a string"),
        ({"database": "prod", "sql": {"query": "SELECT 1"}}, "sql must be a string"),
    ],
)
async def test_sql_query_rejects_invalid_argument_types(
    fake_session, sql_cfg, args, expected_error,
):
    tool = SqlQuery()

    result = await tool.execute(args, fake_session)

    assert result.success is False
    assert result.error == expected_error


@pytest.mark.asyncio
async def test_sql_query_success(fake_session, sql_cfg):
    tool = SqlQuery()
    fake_result = DbGatewayResult(
        success=True,
        csv="id,name\n1,alice\n",
        row_count=1,
        elapsed_ms=50,
        truncated=False,
    )
    with patch("zd_agent.tools.sql_query.query_db_gateway_mcp", AsyncMock(return_value=fake_result)):
        result = await tool.execute(
            {"database": "prod", "sql": "SELECT id, name FROM tenants LIMIT 1"},
            fake_session,
        )
    assert result.success is True
    assert "alice" in result.output


@pytest.mark.asyncio
async def test_sql_query_teleport_expired_from_gateway_marks_session(fake_session, sql_cfg):
    tool = SqlQuery()
    fake_result = DbGatewayResult(
        success=False,
        code="TELEPORT_EXPIRED",
        error="conn refused",
    )
    with patch("zd_agent.tools.sql_query.query_db_gateway_mcp", AsyncMock(return_value=fake_result)):
        result = await tool.execute(
            {"database": "prod", "sql": "SELECT 1"},
            fake_session,
        )
    assert result.success is False
    assert "teleport" in (result.error or "").lower() or "认证" in (result.error or "")
    assert fake_session.waiting_for_auth is True  # session marked


@pytest.mark.asyncio
async def test_sql_query_requires_gateway_config(fake_session, sql_cfg):
    tool = SqlQuery()
    sql_cfg.db_gateway.url = ""
    sql_cfg.db_gateway.api_key = ""
    result = await tool.execute({"database": "prod", "sql": "SELECT 1"}, fake_session)
    assert result.success is False
    assert "DB Gateway" in (result.error or "")


@pytest.mark.asyncio
async def test_sql_query_uses_db_gateway_mcp_when_configured(fake_session, sql_cfg):
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
    ) as gateway_http:
        result = await tool.execute(
            {"database": "prod", "sql": "SELECT id, name FROM tenants LIMIT 1"},
            fake_session,
        )

    assert result.success is True
    assert "alice" in result.output
    gateway_mcp.assert_awaited_once()
    gateway_http.assert_not_awaited()
