from types import SimpleNamespace
from unittest.mock import AsyncMock

from zd_agent.integrations.db_gateway_mcp import DbGatewayResult
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.scoped_queries import (
    TenantScopedSignozQuery,
    TenantScopedSqlQuery,
    scope_postgres_sql,
    scope_signoz_sql,
)


TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
DOCUMENT = "33333333-3333-4333-8333-333333333333"


def test_postgres_scope_accepts_full_tenant_query_and_schema_discovery() -> None:
    scoped = scope_postgres_sql(
        f"SELECT d.id, count(s.id) FROM documents d "
        f"LEFT JOIN document_segments s ON s.document_id=d.id "
        f"AND s.tenant_id='{TENANT}' WHERE d.tenant_id='{TENANT}' GROUP BY d.id",
        tenant=TENANT,
        known_ids=set(),
    )

    assert "documents AS d" in scoped
    assert "document_segments AS s" in scoped
    assert scoped.count(TENANT) == 2
    metadata = scope_postgres_sql(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema='public'",
        tenant=TENANT,
        known_ids=set(),
    )
    assert "information_schema.columns" in metadata

    child = scope_postgres_sql(
        f"SELECT s.id, s.status FROM document_segments s "
        f"WHERE s.document_id='{DOCUMENT}'",
        tenant=TENANT,
        known_ids={DOCUMENT},
    )
    assert "document_segments AS s" in child

    joined_child = scope_postgres_sql(
        f"SELECT d.id, u.name FROM documents d JOIN upload_files u "
        f"ON u.id=d.file_id WHERE d.id='{DOCUMENT}'",
        tenant=TENANT,
        known_ids={DOCUMENT},
    )
    assert "JOIN upload_files AS u ON u.id = d.file_id" in joined_child

    batched = scope_postgres_sql(
        f"SELECT d.id FROM documents d WHERE d.id IN ('{DOCUMENT}')",
        tenant=TENANT,
        known_ids={DOCUMENT},
    )
    assert "IN" in batched


def test_postgres_scope_rejects_unscoped_join_cross_tenant_and_unknown_id() -> None:
    bad_queries = (
        f"SELECT b.email FROM accounts a CROSS JOIN accounts b "
        f"WHERE a.tenant_id='{TENANT}'",
        f"SELECT d.id FROM documents d WHERE d.tenant_id='{OTHER}'",
        f"SELECT d.id FROM documents d WHERE d.tenant_id='{TENANT}' "
        f"AND d.id='{DOCUMENT}'",
        f"SELECT d.id FROM documents d WHERE d.tenant_id='{TENANT}' OR true",
        f"SELECT d.tenant_id='{TENANT}' AS scoped, d.id FROM documents d",
        f"SELECT d.id, a.email FROM documents d JOIN accounts a "
        f"ON (a.id=d.created_by OR true) WHERE d.tenant_id='{TENANT}'",
    )
    for sql in bad_queries:
        try:
            scope_postgres_sql(sql, tenant=TENANT, known_ids=set())
        except ValueError:
            continue
        raise AssertionError(f"unsafe SQL was accepted: {sql}")


def test_postgres_scope_rejects_multiple_statements_mutating_cte_and_unsafe_function() -> (
    None
):
    bad_queries = (
        f"SELECT d.id FROM documents d WHERE d.tenant_id='{TENANT}'; SELECT 2",
        f"WITH changed AS (DELETE FROM documents WHERE tenant_id='{TENANT}' "
        "RETURNING *) SELECT * FROM changed",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT nextval('document_seq')",
    )
    for sql in bad_queries:
        try:
            scope_postgres_sql(sql, tenant=TENANT, known_ids=set())
        except ValueError:
            continue
        raise AssertionError(f"unsafe SQL was accepted: {sql}")


def test_postgres_scope_rejects_unknown_function_but_keeps_common_read_functions() -> (
    None
):
    try:
        scope_postgres_sql(
            "SELECT public.record_support_access()",
            tenant=TENANT,
            known_ids=set(),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unknown PostgreSQL function was accepted")

    scoped = scope_postgres_sql(
        f"SELECT count(d.id), max(d.created_at), now() FROM documents d "
        f"WHERE d.tenant_id='{TENANT}'",
        tenant=TENANT,
        known_ids=set(),
    )
    assert "COUNT(d.id)" in scoped
    assert "MAX(d.created_at)" in scoped


def test_signoz_scope_rewrites_each_source_with_tenant_and_time() -> None:
    scoped = scope_signoz_sql(
        "SELECT severity_text, body FROM signoz_logs.distributed_logs_v2 "
        "WHERE severity_text != 'DEBUG' ORDER BY timestamp DESC LIMIT 100",
        tenant=TENANT,
        known_ids=set(),
    )

    assert "attributes_string['tenant_id']" in scoped
    assert TENANT in scoped
    assert "{start_ns}" in scoped
    assert "{end_ns}" in scoped

    object_scoped = scope_signoz_sql(
        "SELECT body FROM signoz_logs.distributed_logs_v2 ORDER BY timestamp",
        tenant=TENANT,
        known_ids={DOCUMENT},
    )
    assert f"positionCaseInsensitive(body, '{DOCUMENT}') > 0" in object_scoped


def test_signoz_scope_rejects_unknown_table_and_foreign_id() -> None:
    for sql in (
        "SELECT * FROM system.tables",
        "SELECT body FROM signoz_logs.distributed_logs_v2 "
        f"WHERE body LIKE '%{DOCUMENT}%'",
    ):
        try:
            scope_signoz_sql(sql, tenant=TENANT, known_ids=set())
        except ValueError:
            continue
        raise AssertionError(f"unsafe SigNoz SQL was accepted: {sql}")


def test_signoz_scope_rejects_multiple_statements_and_external_table_functions() -> (
    None
):
    bad_queries = (
        "SELECT body FROM signoz_logs.distributed_logs_v2; SELECT 2",
        "SELECT * FROM url('https://attacker.test/data.csv', CSV) JOIN "
        "signoz_logs.distributed_logs_v2 AS logs ON 1 = 1",
        "SELECT * FROM file('/etc/passwd') JOIN "
        "signoz_logs.distributed_logs_v2 AS logs ON 1 = 1",
    )
    for sql in bad_queries:
        try:
            scope_signoz_sql(sql, tenant=TENANT, known_ids=set())
        except ValueError:
            continue
        raise AssertionError(f"unsafe SigNoz SQL was accepted: {sql}")


async def test_sql_tool_remembers_ids_for_same_case_pivots(monkeypatch) -> None:
    gateway = AsyncMock(
        return_value=DbGatewayResult(
            success=True,
            csv=f"document_id\n{DOCUMENT}",
            row_count=1,
            elapsed_ms=5,
        ),
    )
    monkeypatch.setattr(
        "zd_agent.tools.sql_query.get_config",
        lambda: SimpleNamespace(
            db_gateway=SimpleNamespace(
                url="https://db.test",
                api_key="key",
                request_timeout_seconds=30,
            ),
        ),
    )
    monkeypatch.setattr(
        "zd_agent.tools.sql_query.query_db_gateway_mcp",
        gateway,
    )
    session = AgentSession(ticket_id=3880)
    result = await TenantScopedSqlQuery().execute(
        {
            "database": "prod",
            "tenant_id": TENANT,
            "sql": f"SELECT d.id FROM documents d WHERE d.tenant_id='{TENANT}'",
        },
        session,
    )

    assert result.success is True
    assert DOCUMENT in session.investigation_scope_ids

    followup = await TenantScopedSqlQuery().execute(
        {
            "database": "prod",
            "tenant_id": TENANT,
            "sql": f"SELECT d.id FROM documents d WHERE d.tenant_id='{TENANT}' "
            f"AND d.id='{DOCUMENT}'",
        },
        session,
    )
    assert followup.success is True


async def test_signoz_tool_sends_only_rewritten_sql(monkeypatch) -> None:
    run = AsyncMock(return_value=[{"body": "worker received"}])
    monkeypatch.setattr("zd_agent.tools.scoped_queries.run_signoz_sz", run)

    result = await TenantScopedSignozQuery().execute(
        {
            "tenant": TENANT,
            "sql": "SELECT body FROM signoz_logs.distributed_logs_v2 LIMIT 20",
            "hours": 4,
        },
        AgentSession(ticket_id=3880),
    )

    assert result.success is True
    sent_sql = run.await_args.args[1]
    assert TENANT in sent_sql
    assert "{start_ns}" in sent_sql
    assert run.await_args.args[-2:] == ("--hours", "4.0")
