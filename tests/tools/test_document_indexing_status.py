from types import SimpleNamespace
from unittest.mock import AsyncMock

from zd_agent.integrations.db_gateway_mcp import DbGatewayResult
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.document_indexing_status import DocumentIndexingStatus


TENANT = "11111111-1111-4111-8111-111111111111"
DATASET = "22222222-2222-4222-8222-222222222222"
DOCUMENT = "33333333-3333-4333-8333-333333333333"


async def test_document_status_uses_fixed_tenant_scoped_gateway_query(
    monkeypatch,
) -> None:
    gateway = AsyncMock(
        return_value=DbGatewayResult(
            success=True,
            csv=f"document_id,indexing_status\n{DOCUMENT},waiting",
            row_count=1,
            elapsed_ms=12,
        ),
    )
    monkeypatch.setattr(
        "zd_agent.tools.document_indexing_status.get_config",
        lambda: SimpleNamespace(
            db_gateway=SimpleNamespace(url="https://db.test", api_key="key"),
        ),
    )
    monkeypatch.setattr(
        "zd_agent.tools.document_indexing_status.query_db_gateway_mcp",
        gateway,
    )

    session = AgentSession(ticket_id=3880)
    result = await DocumentIndexingStatus().execute(
        {"tenant_id": TENANT, "dataset_id": DATASET, "recent_days": 30},
        session,
    )

    assert result.success is True
    assert f"{DOCUMENT},waiting" in result.output
    assert DOCUMENT in session.investigation_scope_ids
    kwargs = gateway.await_args.kwargs
    assert kwargs["database"] == "prod"
    assert kwargs["ticket_id"] == "3880"
    assert f"d.tenant_id = '{TENANT}'" in kwargs["sql"]
    assert f"d.dataset_id = '{DATASET}'" in kwargs["sql"]
    assert "SELECT now() AS queried_at" in kwargs["sql"]


async def test_document_status_rejects_untrusted_non_uuid_scope(monkeypatch) -> None:
    gateway = AsyncMock()
    monkeypatch.setattr(
        "zd_agent.tools.document_indexing_status.query_db_gateway_mcp",
        gateway,
    )

    result = await DocumentIndexingStatus().execute(
        {"tenant_id": "' OR true --"},
        AgentSession(ticket_id=3880),
    )

    assert result.success is False
    assert "valid UUID" in (result.error or "")
    gateway.assert_not_awaited()
