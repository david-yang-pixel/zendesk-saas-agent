from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any

import pytest

from zd_agent.casework.adapters.tool_investigator import (
    BoundedToolCaseInvestigator,
    InvestigationProtocolError,
    approved_read_tools,
)
from zd_agent.casework.adapters.evidence import tool_evidence
from zd_agent.casework.adapters.model import investigation_result_from_content
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    BillingBindingStatus,
    ClaimRole,
    EvidenceResultStatus,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    BillingBinding,
    BillingSnapshot,
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
from zd_agent.runtime.llm.openai_provider import _messages_to_responses_input
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


NOW = datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"


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
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.is_write_operation = write
        self.output = output
        if parameters is not None:
            self.parameters = parameters
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
    raw: Any = None,
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=calls or [],
        finish_reason="tool_calls" if calls else "stop",
        prompt_tokens=10,
        completion_tokens=5,
        raw=raw,
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
            requester_email="owner@example.test",
        ),
        built_at=NOW,
        identity=identity,
    )


def _unresolved_context() -> CaseContext:
    return _context().model_copy(
        update={
            "identity": IdentityResolution(
                contact_email="owner@example.test",
                target_identity="owner@example.test",
                status=ResolutionStatus.NO_MEMBERSHIP,
            ),
        },
    )


def _stuck_document_context() -> CaseContext:
    return _context().model_copy(
        update={
            "ticket": _context().ticket.model_copy(
                update={
                    "questions": ("Tengo documentos atascados. Ahora están en pausa.",),
                },
            ),
        },
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


def test_terminal_claims_are_deterministically_bound_to_resolved_context() -> None:
    context = _context().model_copy(
        update={
            "billing_required": True,
            "billing": BillingBinding(
                status=BillingBindingStatus.RESOLVED,
                snapshot=BillingSnapshot(
                    tenant_id=TENANT,
                    identity_tenant_id=TENANT,
                    plan="professional",
                    observed_at=NOW,
                    evidence_ids=("ev-identity", "ev-billing"),
                ),
                evidence_ids=("ev-identity", "ev-billing"),
            ),
        },
    )
    raw = json.loads(_final_assessment("ev-runtime"))
    raw["proposed_claims"][0]["role"] = "current_state"
    raw["proposed_claims"][0]["targets"] = []

    result = investigation_result_from_content(
        context=context,
        evidence=(),
        content=json.dumps(raw),
    )

    [claim] = result.proposal.proposed_claims
    assert tuple(target.model_dump() for target in claim.targets) == (
        {"kind": "tenant_id", "value": TENANT},
    )
    assert claim.supporting_evidence_ids == (
        "ev-runtime",
        "ev-identity",
        "ev-billing",
    )


def test_approved_tool_surface_is_explicit_and_write_closed() -> None:
    registry = ToolRegistry()
    docs = FakeTool("official_docs_search")
    database = FakeTool("inspector_exec")
    raw_sql = FakeTool("sql_query")
    raw_signoz = FakeTool("signoz_query")
    deceptive_write = FakeTool("github_get_pr", write=True)
    public_reply = FakeTool("zendesk_public_reply", write=True)
    generic_execution = FakeTool("run_script")
    generic_mcp = FakeTool("custom_mcp_call")
    for tool in (
        docs,
        database,
        raw_sql,
        raw_signoz,
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
    assert result.proposal.proposed_claims[0].supporting_evidence_ids == ("call-docs",)
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
    system_prompt = str(provider.calls[0][0][0]["content"])
    assert "Answer the latest customer question first" in system_prompt
    assert "all customer-useful confirmed facts" in system_prompt
    assert "recovery or next step" in system_prompt
    assert "Do not stop at the first plausible explanation" in system_prompt


async def test_investigator_executes_independent_tool_batch_in_parallel() -> None:
    both_started = asyncio.Event()
    started: list[str] = []

    class CoordinatedTool(FakeTool):
        async def execute(
            self,
            args: dict[str, Any],
            session: AgentSession,
        ) -> ToolResult:
            _ = session
            self.calls.append(args)
            started.append(self.name)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            return ToolResult(success=True, output=self.output)

    registry = ToolRegistry()
    docs = CoordinatedTool("official_docs_search", output="Published behavior")
    github = CoordinatedTool("github_get_issue", output="Issue state")
    registry.register(docs)
    registry.register(github)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-docs",
                        name="official_docs_search",
                        arguments='{"query":"behavior"}',
                    ),
                    ToolCallRequest(
                        id="call-issue",
                        name="github_get_issue",
                        arguments='{"query":"state"}',
                    ),
                ],
            ),
            _response(content=_final_assessment("call-docs")),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=4001),
        clock=FakeClock(),
    )

    result = await asyncio.wait_for(
        investigator.investigate(_context()),
        timeout=0.2,
    )

    assert started == ["official_docs_search", "github_get_issue"]
    assert result.proposal.answer_status is AnswerStatus.SUPPORTED


async def test_investigator_preserves_responses_reasoning_across_tool_rounds() -> None:
    registry = ToolRegistry()
    docs = FakeTool("official_docs_search", output="Published product behavior")
    registry.register(docs)
    prior_output = [
        {
            "id": "rs_1",
            "type": "reasoning",
            "encrypted_content": "encrypted",
            "summary": [],
        },
        {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call-docs",
            "name": "official_docs_search",
            "arguments": '{"query":"supported behavior"}',
            "status": "completed",
        },
    ]
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-docs",
                        name="official_docs_search",
                        arguments='{"query":"supported behavior"}',
                    ),
                ],
                raw={
                    "role": "assistant",
                    "content": None,
                    "_zd_responses_output": prior_output,
                },
            ),
            _response(content=_final_assessment("call-docs")),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=4001),
        clock=FakeClock(),
    )

    await investigator.investigate(_context())

    second_input = _messages_to_responses_input(provider.calls[1][0])
    assert prior_output[0] in second_input
    assert prior_output[1] in second_input
    assert second_input[-1]["type"] == "function_call_output"
    assert second_input[-1]["call_id"] == "call-docs"


_STRICT_SCHEMA = {
    "type": "object",
    "properties": {
        "database": {"type": "string", "enum": ["prod"]},
        "sql": {"type": "string"},
    },
    "required": ["database", "sql"],
}


@pytest.mark.parametrize(
    ("arguments", "error_match"),
    [
        (
            {
                "database": "prod",
                "sql": "SELECT * FROM tenants WHERE id = 'tenant-b'",
                "tenant_id": TENANT,
            },
            "unknown.*tenant_id",
        ),
        ({"database": "prod"}, "missing.*sql"),
        ({"database": "prod", "sql": 42}, "sql.*string"),
    ],
)
async def test_investigator_rejects_schema_invalid_arguments_before_execution(
    arguments: dict[str, Any],
    error_match: str,
) -> None:
    registry = ToolRegistry()
    docs = FakeTool("official_docs_search", parameters=_STRICT_SCHEMA)
    registry.register(docs)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-docs",
                        name="official_docs_search",
                        arguments=json.dumps(arguments),
                    ),
                ],
            ),
            _response(content=_final_assessment("call-docs")),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=4001),
        clock=FakeClock(),
    )

    with pytest.raises(InvestigationProtocolError, match=error_match):
        await investigator.investigate(_context())

    assert docs.calls == []


def test_model_controlled_sql_columns_cannot_create_target_provenance() -> None:
    item = tool_evidence(
        call_id="call-sql",
        tool_name="sql_query",
        args={
            "database": "prod",
            "sql": (f"SELECT * FROM tenants WHERE tenant_id = '{OTHER_TENANT}'"),
            "tenant_id": TENANT,
        },
        allowed_target_arguments=frozenset({"database", "sql"}),
        observed_at=NOW,
        success=True,
        output=(f"Rows: 1 | elapsed: 1ms\n\ntenant_id,status\n{OTHER_TENANT},active"),
        error="",
    )

    assert item.target_value("tenant_id") is None
    assert item.supports_roles == ()


def test_cross_join_sql_cannot_claim_one_tenant_from_where_text() -> None:
    item = tool_evidence(
        call_id="call-sql",
        tool_name="sql_query",
        args={
            "database": "prod",
            "sql": (
                "SELECT target.tenant_id, data.tenant_id AS joined_tenant_id, "
                "data.status FROM tenants target CROSS JOIN workspaces data "
                f"WHERE target.tenant_id = '{TENANT}' "
                f"AND data.tenant_id = '{OTHER_TENANT}'"
            ),
        },
        allowed_target_arguments=frozenset({"database", "sql"}),
        observed_at=NOW,
        success=True,
        output=(
            "Rows: 1 | elapsed: 1ms\n\n"
            "tenant_id,joined_tenant_id,status\n"
            f"{TENANT},{OTHER_TENANT},failing"
        ),
        error="",
    )

    assert item.target_value("tenant_id") is None


async def test_raw_sql_is_not_exposed_even_when_it_spoofs_tenant_column() -> None:
    registry = ToolRegistry()
    sql = FakeTool(
        "sql_query",
        parameters=_STRICT_SCHEMA,
        output=(
            "Rows: 1 | elapsed: 1ms\n\n"
            f"tenant_id,email\n{TENANT},other-tenant@example.test"
        ),
    )
    registry.register(sql)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-sql",
                        name="sql_query",
                        arguments=json.dumps(
                            {
                                "database": "prod",
                                "sql": (
                                    f"SELECT '{TENANT}' AS tenant_id, b.email "
                                    "FROM accounts b WHERE "
                                    f"b.tenant_id = '{OTHER_TENANT}'"
                                ),
                            },
                        ),
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

    assert sql.calls == []


async def test_signoz_traceback_rejects_ignored_tenant_with_exact_id() -> None:
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "tenant": {"type": "string"},
        },
    }
    registry = ToolRegistry()
    traceback = FakeTool("signoz_traceback", parameters=parameters)
    registry.register(traceback)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-traceback",
                        name="signoz_traceback",
                        arguments=json.dumps(
                            {"id": "log-from-other-tenant", "tenant": TENANT},
                        ),
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

    with pytest.raises(
        InvestigationProtocolError, match="cannot combine id and tenant"
    ):
        await investigator.investigate(_context())

    assert traceback.calls == []


@pytest.mark.parametrize(
    ("tool_name", "parameters", "arguments"),
    [
        (
            "inspector_exec",
            {
                "type": "object",
                "properties": {"tenant_id": {"type": "string"}},
                "required": ["tenant_id"],
            },
            {"tenant_id": TENANT},
        ),
        (
            "signoz_errors",
            {
                "type": "object",
                "properties": {"tenant": {"type": "string"}},
            },
            {"tenant": TENANT},
        ),
        (
            "zendesk_get_ticket",
            {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"],
            },
            {"ticket_id": 4001},
        ),
        (
            "ticket_record_read",
            {
                "type": "object",
                "properties": {"ticket_id": {"type": "string"}},
                "required": ["ticket_id"],
            },
            {"ticket_id": "4001"},
        ),
    ],
)
async def test_unresolved_identity_blocks_every_tenant_data_capability(
    tool_name: str,
    parameters: dict[str, Any],
    arguments: dict[str, Any],
) -> None:
    registry = ToolRegistry()
    tool = FakeTool(tool_name, parameters=parameters)
    registry.register(tool)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-sensitive",
                        name=tool_name,
                        arguments=json.dumps(arguments),
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

    with pytest.raises(InvestigationProtocolError, match="requires resolved identity"):
        await investigator.investigate(_unresolved_context())

    assert tool.calls == []


async def test_resolved_identity_rejects_other_tenant_scope_before_execution() -> None:
    parameters = {
        "type": "object",
        "properties": {"tenant_id": {"type": "string"}},
        "required": ["tenant_id"],
    }
    registry = ToolRegistry()
    inspector = FakeTool("inspector_exec", parameters=parameters)
    registry.register(inspector)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-inspector",
                        name="inspector_exec",
                        arguments=json.dumps({"tenant_id": OTHER_TENANT}),
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

    with pytest.raises(InvestigationProtocolError, match="outside the resolved tenant"):
        await investigator.investigate(_context())

    assert inspector.calls == []


@pytest.mark.parametrize(
    ("tool_name", "parameters", "arguments"),
    [
        (
            "inspector_exec",
            {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "section": {"type": "string"},
                },
            },
            {"email": "other-customer@example.test", "section": "tenants"},
        ),
        (
            "inspector_exec",
            {
                "type": "object",
                "properties": {"app_id": {"type": "string"}},
            },
            {"app_id": "app-from-another-tenant"},
        ),
        (
            "signoz_get_trace_details",
            {
                "type": "object",
                "properties": {"traceId": {"type": "string"}},
                "required": ["traceId"],
            },
            {"traceId": "trace-from-another-tenant"},
        ),
        (
            "signoz_traceback",
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "tenant": {"type": "string"},
                },
            },
            {"id": "log-from-another-tenant"},
        ),
        (
            "zendesk_get_ticket",
            {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"],
            },
            {"ticket_id": 4002},
        ),
        (
            "zendesk_get_user",
            {
                "type": "object",
                "properties": {"user_id": {"type": "integer"}},
                "required": ["user_id"],
            },
            {"user_id": 999},
        ),
        (
            "zendesk_search",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            {"query": "requester:other-customer@example.test"},
        ),
        (
            "ticket_record_read",
            {
                "type": "object",
                "properties": {"ticket_id": {"type": "string"}},
                "required": ["ticket_id"],
            },
            {"ticket_id": "4002"},
        ),
        (
            "ticket_record_search",
            {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["keywords"],
            },
            {"keywords": ["other customer"]},
        ),
    ],
)
async def test_capability_acl_rejects_untrusted_cross_customer_reads_before_execution(
    tool_name: str,
    parameters: dict[str, Any],
    arguments: dict[str, Any],
) -> None:
    registry = ToolRegistry()
    tool = FakeTool(tool_name, parameters=parameters)
    registry.register(tool)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-sensitive",
                        name=tool_name,
                        arguments=json.dumps(arguments),
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

    with pytest.raises(InvestigationProtocolError, match="current case capability"):
        await investigator.investigate(_context())

    assert tool.calls == []


@pytest.mark.parametrize(
    ("tool_name", "parameters", "arguments"),
    [
        (
            "inspector_exec",
            {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string"},
                    "section": {"type": "string"},
                },
            },
            {"tenant_id": TENANT, "section": "runs"},
        ),
        (
            "inspector_exec",
            {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "section": {"type": "string"},
                },
            },
            {"email": "OWNER@example.test", "section": "tenants"},
        ),
        (
            "signoz_errors",
            {
                "type": "object",
                "properties": {"tenant": {"type": "string"}},
            },
            {"tenant": TENANT},
        ),
        (
            "signoz_search_logs",
            {
                "type": "object",
                "properties": {
                    "tenant": {"type": "string"},
                    "traceId": {"type": "string"},
                },
            },
            {"tenant": TENANT, "traceId": "tenant-scoped-trace"},
        ),
        (
            "zendesk_get_ticket",
            {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"],
            },
            {"ticket_id": 4001},
        ),
        (
            "ticket_record_read",
            {
                "type": "object",
                "properties": {"ticket_id": {"type": "string"}},
                "required": ["ticket_id"],
            },
            {"ticket_id": "4001"},
        ),
    ],
)
async def test_capability_acl_executes_only_trusted_current_case_reads(
    tool_name: str,
    parameters: dict[str, Any],
    arguments: dict[str, Any],
) -> None:
    registry = ToolRegistry()
    tool = FakeTool(tool_name, parameters=parameters)
    registry.register(tool)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-trusted",
                        name=tool_name,
                        arguments=json.dumps(arguments),
                    ),
                ],
            ),
            _response(
                content=json.dumps(
                    {
                        "answer_status": "insufficient",
                        "proposed_claims": [],
                        "proposed_customer_fields": [],
                        "action_proposal": None,
                    },
                ),
            ),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=4001),
        clock=FakeClock(),
    )

    await investigator.investigate(_context())

    assert tool.calls == [arguments]


async def test_signoz_capability_injects_the_code_resolved_tenant() -> None:
    registry = ToolRegistry()
    aggregate = FakeTool(
        "signoz_aggregate_logs",
        parameters={
            "type": "object",
            "properties": {
                "tenant": {"type": "string"},
                "aggregation": {"type": "string"},
            },
            "required": ["aggregation"],
        },
    )
    registry.register(aggregate)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-signoz",
                        name="signoz_aggregate_logs",
                        arguments=json.dumps({"aggregation": "count"}),
                    ),
                ],
            ),
            _response(
                content=json.dumps(
                    {
                        "answer_status": "insufficient",
                        "proposed_claims": [],
                        "proposed_customer_fields": [],
                        "action_proposal": None,
                    },
                ),
            ),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=4001),
        clock=FakeClock(),
    )

    await investigator.investigate(_context())

    assert aggregate.calls == [{"aggregation": "count", "tenant": TENANT}]


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


async def test_investigator_converges_when_tool_call_batch_exceeds_budget() -> None:
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
            _response(
                content=json.dumps(
                    {
                        "answer_status": "insufficient",
                        "proposed_claims": [],
                        "proposed_customer_fields": [],
                        "action_proposal": None,
                    },
                ),
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

    result = await investigator.investigate(_context())

    assert docs.calls == []
    assert result.proposal.answer_status is AnswerStatus.INSUFFICIENT
    assert len(provider.calls) == 2
    terminal_messages, terminal_tools = provider.calls[-1]
    assert terminal_tools is None
    assert "tool-call budget is exhausted" in terminal_messages[-1]["content"]
    assert [message["role"] for message in terminal_messages[-3:]] == [
        "tool",
        "tool",
        "user",
    ]


async def test_investigator_converges_when_round_budget_is_exhausted() -> None:
    registry = ToolRegistry()
    docs = FakeTool("official_docs_search")
    registry.register(docs)
    provider = FakeProvider(
        [
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-docs",
                        name="official_docs_search",
                        arguments="{}",
                    ),
                ],
            ),
            _response(
                content=json.dumps(
                    {
                        "answer_status": "insufficient",
                        "proposed_claims": [],
                        "proposed_customer_fields": [],
                        "action_proposal": None,
                    },
                ),
            ),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=4001),
        clock=FakeClock(),
        max_rounds=1,
    )

    result = await investigator.investigate(_context())

    assert docs.calls == [{}]
    assert result.proposal.answer_status is AnswerStatus.INSUFFICIENT
    terminal_messages, terminal_tools = provider.calls[-1]
    assert terminal_tools is None
    assert "round budget is exhausted" in terminal_messages[-1]["content"]


async def test_investigator_enforces_cumulative_model_token_budget() -> None:
    registry = ToolRegistry()
    provider = FakeProvider([_response(content=_final_assessment("ev-identity"))])
    session = AgentSession(ticket_id=4001)
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=session,
        clock=FakeClock(),
        max_tokens=14,
    )

    with pytest.raises(InvestigationProtocolError, match="token budget"):
        await investigator.investigate(_context())

    assert session.token_usage.total_tokens == 15


async def test_investigator_does_not_call_model_after_vision_exhausts_budget() -> None:
    registry = ToolRegistry()
    provider = FakeProvider([_response(content=_final_assessment("ev-identity"))])
    session = AgentSession(ticket_id=4001)
    session.token_usage.prompt_tokens = 10
    session.token_usage.completion_tokens = 5
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=session,
        clock=FakeClock(),
        max_tokens=15,
    )

    with pytest.raises(InvestigationProtocolError, match="token budget"):
        await investigator.investigate(_context())

    assert provider.calls == []


async def test_runtime_skill_forces_more_investigation_before_terminal_answer() -> None:
    registry = ToolRegistry()
    status = FakeTool(
        "document_indexing_status",
        parameters={
            "type": "object",
            "properties": {"tenant_id": {"type": "string"}},
            "required": ["tenant_id"],
        },
    )
    registry.register(status)
    current_only = json.dumps(
        {
            "answer_status": "bounded",
            "proposed_claims": [
                {
                    "statement": "The documents are currently waiting.",
                    "role": "current_state",
                    "status": "confirmed",
                    "supporting_evidence_ids": ["ev-identity"],
                },
            ],
            "proposed_customer_fields": [],
            "action_proposal": None,
        },
    )
    complete = json.dumps(
        {
            "answer_status": "bounded",
            "proposed_claims": [
                {
                    "statement": "The documents are currently waiting.",
                    "role": "current_state",
                    "status": "confirmed",
                    "supporting_evidence_ids": ["call-status"],
                },
                {
                    "statement": "Processing has not started for those documents.",
                    "role": "failing_boundary",
                    "status": "confirmed",
                    "supporting_evidence_ids": ["call-status"],
                },
            ],
            "proposed_customer_fields": [],
            "action_proposal": None,
        },
    )
    provider = FakeProvider(
        [
            _response(content=current_only),
            _response(
                calls=[
                    ToolCallRequest(
                        id="call-status",
                        name="document_indexing_status",
                        arguments="{}",
                    ),
                ],
            ),
            _response(content=complete),
        ],
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=AgentSession(ticket_id=3880),
        clock=FakeClock(),
    )

    result = await investigator.investigate(_stuck_document_context())

    assert status.calls == [{"tenant_id": TENANT}]
    assert {claim.role for claim in result.proposal.proposed_claims} == {
        ClaimRole.CURRENT_STATE,
        ClaimRole.FAILING_BOUNDARY,
    }
    corrective_messages, _ = provider.calls[1]
    correction = corrective_messages[-1]["content"]
    assert "Missing material claim roles:" in correction
    assert "current_state" in correction
    assert "failing_boundary" in correction
    first_messages, _ = provider.calls[0]
    assert "ACTIVE_SCENARIO=async_processing" in first_messages[0]["content"]
    assert "document_indexing_status" in first_messages[0]["content"]
