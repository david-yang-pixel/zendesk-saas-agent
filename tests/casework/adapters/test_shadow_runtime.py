import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zd_agent.casework.adapters.archive import JsonlShadowArchive
from zd_agent.casework.adapters.shadow import ShadowRuntime
from zd_agent.casework.adapters.legacy_tools import SystemClock
from zd_agent.casework.domain.enums import (
    Disposition,
    OwnershipAction,
    ReplyKind,
)
from zd_agent.casework.domain.shadow import ShadowFailureRecord, ShadowRecord
from zd_agent.casework.domain.models import TicketSnapshot, VisualObservation
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolSchema
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


class FixedClock(SystemClock):
    def now(self) -> datetime:
        return NOW


class FakeTicketLoader:
    async def load(
        self,
        ticket_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert ticket_id == 4001
        return (
            [
                {
                    "id": "event-1",
                    "created_at": "2026-08-11T12:00:00Z",
                    "body": "What subscription is active for this workspace?",
                    "form_data": {"email": "owner@example.test"},
                    "image_urls": [],
                },
            ],
            {"status": "open", "via": {"channel": "messaging"}},
        )


class NoVisuals:
    async def analyze(
        self,
        ticket: TicketSnapshot,
    ) -> tuple[VisualObservation, ...]:
        _ = ticket
        return ()


class FakeInspector(Tool):
    name = "inspector_exec"
    description = "read-only inspector"
    parameters: dict[str, object] = {"type": "object"}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        self.calls.append(args)
        if args.get("section") == "tenants":
            return ToolResult(
                output="EMAIL_WORKSPACE_RESOLUTION=unique",
                metadata={
                    "workspace_resolution": {
                        "source": "prod_membership",
                        "status": "unique",
                        "count": 1,
                        "tenant_ids": [TENANT],
                        "candidates": [
                            {"tenant_id": TENANT, "name": "Example Workspace"},
                        ],
                    },
                },
            )
        return ToolResult(
            output=(
                "AUTHORITATIVE_BILLING_PLAN=professional\n"
                "AUTHORITATIVE_BILLING_STATUS=active\n"
                "AUTHORITATIVE_BILLING_SOURCE=billing_db"
            ),
        )


class PromptAwareProvider(LLMProvider):
    def __init__(self, *, malformed: bool = False) -> None:
        self.malformed = malformed
        self.calls = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls += 1
        if self.malformed:
            content = "not-json"
        else:
            prompt = json.loads(str(messages[-1]["content"]))
            billing = next(
                item
                for item in prompt["eligible_evidence"]
                if item["source"] == "case_context.billing"
            )
            content = json.dumps(
                {
                    "answer_status": "supported",
                    "proposed_claims": [
                        {
                            "statement": "The workspace currently has an active Professional subscription.",
                            "role": "current_state",
                            "status": "confirmed",
                            "supporting_evidence_ids": [billing["evidence_id"]],
                            "targets": [{"kind": "tenant_id", "value": TENANT}],
                        },
                    ],
                    "proposed_customer_fields": [],
                    "action_proposal": None,
                },
            )
        return LLMResponse(
            content=content,
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=10,
        )

    def format_tool_result(
        self,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _runtime(
    tmp_path: Path,
    *,
    provider: LLMProvider,
) -> tuple[ShadowRuntime, AgentSession, FakeInspector]:
    session = AgentSession(ticket_id=4001)
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    session.public_reply_type = "handoff"
    session.customer_profile = {"requester_email": "requester@example.test"}
    inspector = FakeInspector()
    registry = ToolRegistry()
    registry.register(inspector)
    runtime = ShadowRuntime(
        session=session,
        provider=provider,
        registry=registry,
        archive=JsonlShadowArchive(tmp_path),
        ticket_loader=FakeTicketLoader(),
        visual_analyzer=NoVisuals(),
        clock=FixedClock(),
    )
    return runtime, session, inspector


async def test_shadow_runtime_evaluates_current_ticket_without_delivery(tmp_path) -> None:
    runtime, _, inspector = _runtime(tmp_path, provider=PromptAwareProvider())

    record = await runtime.run_best_effort()

    assert isinstance(record, ShadowRecord)
    assert record.disposition is Disposition.RESOLVED
    assert record.reply_kind is ReplyKind.ANSWER
    assert record.ownership_action is OwnershipAction.NONE
    assert record.delivery_attempted is False
    assert record.legacy_reply_type == "handoff"
    assert record.decision_disagrees is True
    assert inspector.calls == [
        {"email": "owner@example.test", "section": "tenants"},
        {"tenant_id": TENANT, "section": "billing"},
    ]
    assert (tmp_path / "shadow-decisions.jsonl").exists()


async def test_shadow_runtime_archives_failure_without_affecting_legacy_session(
    tmp_path: Path,
) -> None:
    runtime, session, _ = _runtime(
        tmp_path,
        provider=PromptAwareProvider(malformed=True),
    )

    record = await runtime.run_best_effort()

    assert isinstance(record, ShadowFailureRecord)
    assert record.stage == "shadow_evaluation"
    assert session.status is SessionStatus.COMPLETED
    assert session.reply_sent is True
    assert record.delivery_attempted is False
