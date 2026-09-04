from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from casework_agent_core.domain import TicketComment, TicketSnapshot
from casework_harness.delivery import (
    DeliveryController,
    DeliveryDisposition,
    DeliveryMode,
    DeliveryPolicy,
    DeliveryRequest,
)
from casework_harness.footer import FooterCatalog
from casework_harness.sandbox_controller import (
    SandboxController,
    SandboxExecution,
    SandboxExecutionStatus,
)
from casework_ingress.app import create_app
from casework_ingress.fakes import InMemoryJobSink
from casework_ingress.processing_rules import ProcessingRuleConfig
from casework_sandbox.fakes import FakeModel, FakeZendesk
from casework_sandbox.runtime import RuntimeOutcome, SandboxAgent
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.tool import ToolDefinition, ToolResult, ToolResultStatus


class _TypedTestClient(Protocol):
    def post(self, url: str, *, json: object) -> httpx.Response: ...


class _EvidenceTool:
    definition = ToolDefinition(
        name="ticket_evidence",
        description="Return sanitized ticket evidence",
        input_schema={"type": "object"},
    )

    async def __call__(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del arguments
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary=f"Evidence for ticket {context.ticket_id}",
            data={"test": True},
            evidence_id="ev-sanitized",
            observed_at=datetime.now(UTC),
        )


class _LocalSandboxEngine:
    def __init__(self, agent: SandboxAgent) -> None:
        self.agent = agent
        self.destroyed: list[str] = []
        self.created: list[str] = []

    async def create(
        self, *, image: str, limits: dict[str, int], labels: dict[str, str]
    ) -> str:
        del image, limits, labels
        sandbox_id = f"sandbox-{len(self.created) + 1}"
        self.created.append(sandbox_id)
        return sandbox_id

    async def wait_ready(self, sandbox_id: str, *, timeout_seconds: int) -> None:
        del sandbox_id, timeout_seconds

    async def execute(
        self,
        sandbox_id: str,
        *,
        request: dict[str, Any],
        timeout_seconds: int,
    ) -> SandboxExecution:
        del sandbox_id, timeout_seconds
        outcome = await self.agent.run(
            ticket_id=int(request["ticket_id"]),
            session_id=str(request["session_id"]),
        )
        return SandboxExecution(
            status=SandboxExecutionStatus.SUCCEEDED,
            result=outcome.model_dump(mode="json"),
        )

    async def destroy(self, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)


class _Freshness:
    async def latest(self, ticket_id: int) -> tuple[int, str]:
        assert ticket_id == 9001
        return 1, "customer-ja-1"


class _WriteTrap:
    async def create_or_get(self, **kwargs: object) -> object:
        raise AssertionError(f"dry run touched outbox: {kwargs}")

    async def claim(self, **kwargs: object) -> bool:
        raise AssertionError(f"dry run claimed outbox: {kwargs}")

    async def cancel(self, **kwargs: object) -> None:
        raise AssertionError(f"dry run cancelled outbox: {kwargs}")

    async def mark_sent(self, **kwargs: object) -> None:
        raise AssertionError(f"dry run marked outbox: {kwargs}")


class _ZendeskWriteTrap:
    async def send_reply(
        self, *, ticket_id: int, body: str, idempotency_key: str
    ) -> object:
        raise AssertionError(
            f"dry run wrote Zendesk ticket {ticket_id}: {idempotency_key} {body}"
        )


@pytest.mark.asyncio
async def test_webhook_to_ephemeral_sandbox_no_write_dry_run() -> None:
    jobs = InMemoryJobSink()
    client = cast(
        _TypedTestClient,
        TestClient(
            create_app(
                config=ProcessingRuleConfig(
                    saas_tag="saas", saas_assignee="saas-support"
                ),
                job_sink=jobs,
            )
        ),
    )
    response = client.post(
        "/webhook/zendesk",
        json={
            "event_id": "customer-ja-1",
            "ticket_id": 9001,
            "tags": ["saas"],
            "assignee": "saas-support",
            "status": "open",
            "is_customer_message": True,
            "actor": "customer",
            "visibility": "public",
            "message_text": "日本語で回答できますか？",
        },
    )
    assert response.json()["action"] == "enqueue"
    assert [job.ticket_id for job in jobs.submitted] == [9001]

    snapshot = TicketSnapshot(
        ticket_id=9001,
        status="open",
        requester_id=1,
        assignee_id=2,
        tags=("saas",),
        messages=(
            TicketComment(
                event_id="customer-ja-1",
                author_origin="customer",
                public=True,
                body="日本語で回答できますか？",
                created_at=datetime.now(UTC),
            ),
        ),
        locale="ja-JP",
        latest_customer_cursor="customer-ja-1",
        complete=True,
        fetched_at=datetime.now(UTC),
    )
    tools = ToolRegistry()
    tools.register(_EvidenceTool.definition, _EvidenceTool())
    engine = _LocalSandboxEngine(
        SandboxAgent(
            zendesk=FakeZendesk(snapshot),
            model=FakeModel(answer="はい、日本語で回答できます。"),
            tools=tools,
        )
    )
    sandbox_result = await SandboxController(
        engine=engine,
        image="casework-sandbox@sha256:test",
    ).run(
        {
            "job_id": "job-replay-1",
            "session_id": "session-replay-1",
            "ticket_id": jobs.submitted[0].ticket_id,
            "generation": 1,
            "config_revision": "config-1",
        }
    )
    assert sandbox_result.result is not None
    outcome = RuntimeOutcome.model_validate(sandbox_result.result)

    delivery = await DeliveryController(
        freshness=_Freshness(),
        outbox=_WriteTrap(),
        sender=_ZendeskWriteTrap(),
        footer_catalog=FooterCatalog(
            default_locale="en-US",
            footers={
                "en-US": "This reply was prepared by Dify AI Agent.",
                "ja-JP": "この返信は Dify AI Agent によって作成されました。",
            },
        ),
        policy=DeliveryPolicy(mode=DeliveryMode.DRY_RUN),
    ).deliver(DeliveryRequest(outcome=outcome, generation=1))

    assert delivery.disposition is DeliveryDisposition.DRY_RUN
    assert delivery.body is not None
    assert delivery.body.startswith("はい、日本語で回答できます。")
    assert delivery.body.endswith(
        "この返信は Dify AI Agent によって作成されました。"
    )
    assert engine.created == ["sandbox-1"]
    assert engine.destroyed == ["sandbox-1"]
