from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import pytest

from zd_agent.casework.adapters.tool_investigator import (
    BoundedToolCaseInvestigator,
    InvestigationProtocolError,
    approved_read_tools,
)
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    ClaimRole,
    EvidenceResultStatus,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    CaseContext,
    FormIdentity,
    IdentityResolution,
    TicketSnapshot,
    WorkspaceCandidate,
)
from zd_agent.runtime.llm.types import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


NOW = datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeTool(Tool):
    description = "Read current evidence."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }

    def __init__(
        self,
        name: str,
        *,
        write: bool = False,
        output: str = "current tool evidence",
    ) -> None:
        self.name = name
        self.is_write_operation = write
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        _ = session
        self.calls.append(args)
        return ToolResult(success=True, output=self.output)


class FakeProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[list[dict[str, Any]], list[ToolSchema] | None]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        _ = temperature
        self.calls.append((list(messages), tools))
        return self.responses.pop(0)

    def format_tool_result(
        self,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }


def _response(
    *,
    content: str | None = None,
    calls: list[ToolCallRequest] | None = None,
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=calls or [],
        finish_reason="tool_calls" if calls else "stop",
        prompt_tokens=10,
        completion_tokens=5,
    )


def _context() -> CaseContext:
    identity = IdentityResolution(
        contact_email="owner@example.test",
        target_identity="owner@example.test",
        status=ResolutionStatus.RESOLVED,
        method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
        candidates=(
            WorkspaceCandidate(
                tenant_id=TENANT,
                workspace_name="Workspace",
                role="owner",
            ),
        ),
        selected_tenant_id=TENANT,
        selected_workspace_name="Workspace",
        selected_role="owner",
        evidence_ids=("ev-identity",),
    )
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=2,
            status="open",
            latest_event_id="event-2",
            latest_event_at=NOW,
            questions=("Is this behavior supported?",),
            form_identity=FormIdentity(email="owner@example.test"),
        ),
        built_at=NOW,
        identity=identity,
    )


def _final_assessment(evidence_id: str) -> str:
    return json.dumps(
        {
            "answer_status": "supported",
            "proposed_claims": [
                {
                    "statement": "This behavior is supported in the current product.",
                    "role": "product_fact",
                    "status": "confirmed",
                    "material": True,
                    "supporting_evidence_ids": [evidence_id],
                    "targets": [],
                },
            ],
            "proposed_customer_fields": [],
            "action_proposal": None,
        },
    )


def test_approved_tool_surface_is_explicit_and_write_closed() -> None:
    registry = ToolRegistry()
    docs = FakeTool("official_docs_search")
    database = FakeTool("inspector_exec")
    deceptive_write = FakeTool("github_get_pr", write=True)
    public_reply = FakeTool("zendesk_public_reply", write=True)
    generic_execution = FakeTool("run_script")
    generic_mcp = FakeTool("custom_mcp_call")
    for tool in (
        docs,
        database,
        deceptive_write,
        public_reply,
        generic_execution,
        generic_mcp,
    ):
        registry.register(tool)

    approved = approved_read_tools(registry)

    assert tuple(tool.name for tool in approved) == (
        "official_docs_search",
        "inspector_exec",
    )


async def test_investigator_calls_tools_itself_and_returns_typed_evidence() -> None:
    registry = ToolRegistry()
    docs = FakeTool("official_docs_search", output="Published product behavior")
    registry.register(docs)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-docs",
                        name="official_docs_search",
                        arguments=json.dumps({"query": "supported behavior"}),
                    ),
                ],
            ),
            _response(content=_final_assessment("call-docs")),
        ],
    )
    session = AgentSession(ticket_id=4001)
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=session,
        clock=FakeClock(),
        max_rounds=4,
        max_calls=6,
    )

    result = await investigator.investigate(_context())

    assert docs.calls == [{"query": "supported behavior"}]
    assert result.proposal.answer_status is AnswerStatus.SUPPORTED
    assert result.proposal.proposed_claims[0].supporting_evidence_ids == (
        "call-docs",
    )
    docs_evidence = next(
        item for item in result.evidence if item.evidence_id == "call-docs"
    )
    assert docs_evidence.source == "official_docs_search"
    assert docs_evidence.result_status is EvidenceResultStatus.SUCCESS
    assert ClaimRole.PRODUCT_FACT in docs_evidence.supports_roles
    assert docs_evidence.payload == "Published product behavior"
    second_messages = provider.calls[1][0]
    assert "EVIDENCE_ID=call-docs" in str(second_messages[-1]["content"])
    assert session.tool_call_log[0]["call_id"] == "call-docs"


async def test_investigator_fails_closed_on_unexposed_or_write_tool_call() -> None:
    registry = ToolRegistry()
    public_reply = FakeTool("zendesk_public_reply", write=True)
    registry.register(public_reply)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-write",
                        name="zendesk_public_reply",
                        arguments="{}",
                    ),
                ],
            ),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=4001),
        clock=FakeClock(),
    )

    with pytest.raises(InvestigationProtocolError, match="not approved"):
        await investigator.investigate(_context())

    assert public_reply.calls == []


async def test_investigator_enforces_total_tool_call_budget_before_execution() -> None:
    registry = ToolRegistry()
    docs = FakeTool("official_docs_search")
    registry.register(docs)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-1",
                        name="official_docs_search",
                        arguments="{}",
                    ),
                    ToolCallRequest(
                        id="call-2",
                        name="official_docs_search",
                        arguments="{}",
                    ),
                ],
            ),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=4001),
        clock=FakeClock(),
        max_calls=1,
    )

    with pytest.raises(InvestigationProtocolError, match="call budget"):
        await investigator.investigate(_context())

    assert docs.calls == []
