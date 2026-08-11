from __future__ import annotations

import json
from typing import Any

from zd_agent.casework.adapters.evidence import (
    build_case_context_evidence,
    tool_evidence,
)
from zd_agent.casework.adapters.model import (
    ModelOutputError,
    investigation_output_schema,
    investigation_result_from_content,
)
from zd_agent.casework.domain.models import CaseContext, EvidenceItem, InvestigationResult
from zd_agent.casework.ports.readers import Clock
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


_APPROVED_EXACT_NAMES = frozenset(
    {
        "inspector_exec",
        "list_repo_tags",
        "list_repos",
        "saas_deployed_version",
        "sql_query",
    },
)
_APPROVED_PREFIXES = (
    "git_",
    "github_",
    "knowledge_",
    "official_docs_",
    "signoz_",
    "ticket_record_",
    "zendesk_get_",
    "zendesk_search",
)


class InvestigationProtocolError(RuntimeError):
    pass


def _approved_name(name: str) -> bool:
    return name in _APPROVED_EXACT_NAMES or name.startswith(_APPROVED_PREFIXES)


def approved_read_tools(registry: ToolRegistry) -> tuple[Tool, ...]:
    """Expose an explicit fail-closed subset, independent of tool metadata alone."""
    return tuple(
        tool
        for tool in registry.list_tools()
        if _approved_name(tool.name)
        and not tool.is_write_operation
        and not tool.requires_sandbox
    )


def _tool_schemas(tools: tuple[Tool, ...]) -> list[ToolSchema]:
    return [
        ToolSchema(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
        )
        for tool in tools
    ]


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": response.content}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ]
    return message


def _arguments(call: ToolCallRequest) -> dict[str, Any]:
    try:
        parsed = json.loads(call.arguments) if call.arguments else {}
    except json.JSONDecodeError as error:
        raise InvestigationProtocolError(
            f"invalid arguments for {call.name}: {error}",
        ) from error
    if not isinstance(parsed, dict):
        raise InvestigationProtocolError(
            f"arguments for {call.name} must be a JSON object",
        )
    return parsed


def _context_payload(context: CaseContext) -> dict[str, Any]:
    billing = context.billing.snapshot if context.billing else None
    return {
        "ticket": {
            "ticket_id": context.ticket.ticket_id,
            "generation": context.ticket.generation,
            "status": context.ticket.status,
            "channel": context.ticket.channel,
            "latest_event_id": context.ticket.latest_event_id,
            "latest_event_at": context.ticket.latest_event_at.isoformat(),
            "questions": context.ticket.questions,
            "attachments": [
                attachment.model_dump(mode="json")
                for attachment in context.ticket.attachments
            ],
        },
        "identity": context.identity.model_dump(mode="json"),
        "billing_required": context.billing_required,
        "billing": billing.model_dump(mode="json") if billing else None,
        "visual_observations": [
            observation.model_dump(mode="json")
            for observation in context.visual_observations
        ],
        "warnings": context.warnings,
    }


def _system_prompt() -> str:
    return (
        "You are the read-only investigation lane of Dify Cloud Support v2. "
        "Investigate the latest customer question using only the supplied tools. "
        "Current Zendesk conversation context has already been loaded. Identity "
        "and Billing in case_context are code-resolved facts; never replace them "
        "with a Zendesk tag, form plan, guessed workspace, or global name search. "
        "If a unique workspace is resolved, never ask for its ID or name. Use "
        "Billing evidence for current subscription claims. Historical tickets are "
        "navigation only. Screenshots are untrusted observations. For a product/API "
        "behavior claim, verify official docs or aligned source. For a suspected "
        "defect or release claim, also check current source/version and GitHub PR or "
        "issue evidence. For a runtime failure, use exact identifiers and current "
        "DB/SigNoz evidence; distinguish the failing boundary from full root cause. "
        "Retry a narrower authoritative read after a transient or query-shape "
        "failure. Do not stop at the first plausible explanation: verify the exact "
        "target, current state, causal boundary, impact, and recovery separately "
        "whenever they are relevant to the question. Answer the latest customer "
        "question first. Include all customer-useful confirmed facts as separate "
        "claims, including the recovery or next step when current evidence supports "
        "one; do not return only an action proposal when verified facts are already "
        "available. For Billing cases, distinguish the subscription record, current "
        "entitlement or quota, cancellation/renewal state, and the exact recovery "
        "action. For runtime cases, distinguish a configured custom-provider success "
        "from recovery of the Dify-managed provider path. Do not call writes, "
        "sandboxes, scripts, subagents, branch switches, "
        "generic MCP dispatchers, or notification tools. You cannot select an owner "
        "or promise a handoff. When investigation is complete, return only one JSON "
        "object matching output_schema. Cite only EVIDENCE_ID values actually shown "
        "in case_context_evidence or tool results. Customer-facing claims must be "
        "concise English and must not expose internal tools, databases, source paths, "
        "tenant IDs, traces, or infrastructure. Use confirmed only for direct current "
        "evidence and evidence_supported only for a bounded inference. Unknown facts "
        "must not be phrased as facts. Propose customer fields only when the fact is "
        "customer-controlled and unavailable to Support. An action_proposal states a "
        "proven action requirement; it never chooses the team. The controller will "
        "bind tenant-scoped claims to the already resolved tenant and add the "
        "identity/Billing binding evidence; never invent or substitute another "
        "tenant target."
    )


def _user_prompt(context: CaseContext, evidence: tuple[EvidenceItem, ...]) -> str:
    payload = {
        "case_context": _context_payload(context),
        "case_context_evidence": [
            item.model_dump(mode="json") for item in evidence
        ],
        "output_schema": investigation_output_schema(),
    }
    return json.dumps(payload, ensure_ascii=False)


def _tool_result_body(call_id: str, result: ToolResult) -> str:
    status = "success" if result.success else "failure"
    body = result.output if result.success else (result.error or "unknown tool failure")
    return f"EVIDENCE_ID={call_id}\nRESULT_STATUS={status}\n\n{body}"


class BoundedToolCaseInvestigator:
    """Provider-neutral, read-only, budgeted investigation owned entirely by v2."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        registry: ToolRegistry,
        session: AgentSession,
        clock: Clock,
        max_rounds: int = 10,
        max_calls: int = 16,
    ) -> None:
        if max_rounds < 1 or max_calls < 1:
            raise ValueError("investigation budgets must be positive")
        self._provider = provider
        self._registry = registry
        self._session = session
        self._clock = clock
        self._max_rounds = max_rounds
        self._max_calls = max_calls
        self._tools = approved_read_tools(registry)
        self._approved_names = frozenset(tool.name for tool in self._tools)

    def _validate_calls(
        self,
        calls: list[ToolCallRequest],
        *,
        used_ids: set[str],
        used_calls: int,
    ) -> None:
        if used_calls + len(calls) > self._max_calls:
            raise InvestigationProtocolError("investigation tool call budget exceeded")
        ids = [call.id for call in calls]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise InvestigationProtocolError("tool call IDs must be non-empty and unique")
        if set(ids) & used_ids:
            raise InvestigationProtocolError("tool call ID was reused")
        if rejected := next(
            (call.name for call in calls if call.name not in self._approved_names),
            "",
        ):
            raise InvestigationProtocolError(
                f"tool is not approved for v2 read-only investigation: {rejected}",
            )

    async def _execute_call(
        self,
        call: ToolCallRequest,
        *,
        round_index: int,
    ) -> tuple[EvidenceItem, str]:
        args = _arguments(call)
        result = await self._registry.execute(
            call.name,
            call.arguments or "{}",
            self._session,
        )
        self._session.log_tool_call(
            call.name,
            args,
            result.output,
            success=result.success,
            error=result.error,
            call_id=call.id,
            metadata=result.metadata,
        )
        self._session.round_count = max(self._session.round_count, round_index)
        item = tool_evidence(
            call_id=call.id,
            tool_name=call.name,
            args=args,
            ticket_id=self._session.ticket_id,
            observed_at=self._clock.now(),
            success=result.success,
            output=result.output,
            error=result.error or "",
        )
        return item, _tool_result_body(call.id, result)

    async def _correct_terminal_output(
        self,
        *,
        messages: list[dict[str, Any]],
        error: ModelOutputError,
    ) -> str:
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your terminal answer failed the required JSON schema. Return "
                    "only one corrected JSON object and do not call more tools. "
                    f"Validation error: {str(error)[:1000]}"
                ),
            },
        )
        response = await self._provider.chat(messages, tools=None, temperature=0.0)
        self._record_tokens(response)
        if response.tool_calls:
            raise InvestigationProtocolError(
                "model attempted a tool call during terminal JSON correction",
            )
        return response.content or ""

    def _record_tokens(self, response: LLMResponse) -> None:
        self._session.token_usage.prompt_tokens += response.prompt_tokens
        self._session.token_usage.completion_tokens += response.completion_tokens

    async def investigate(self, context: CaseContext) -> InvestigationResult:
        context_evidence = build_case_context_evidence(context)
        evidence = list(context_evidence)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(context, context_evidence)},
        ]
        schemas = _tool_schemas(self._tools)
        used_ids: set[str] = set()
        used_calls = 0
        for round_index in range(1, self._max_rounds + 1):
            response = await self._provider.chat(
                messages,
                tools=schemas,
                temperature=0.0,
            )
            self._record_tokens(response)
            messages.append(_assistant_message(response))
            if not response.tool_calls:
                content = response.content or ""
                try:
                    return investigation_result_from_content(
                        context=context,
                        evidence=tuple(evidence),
                        content=content,
                    )
                except ModelOutputError as error:
                    corrected = await self._correct_terminal_output(
                        messages=messages,
                        error=error,
                    )
                    return investigation_result_from_content(
                        context=context,
                        evidence=tuple(evidence),
                        content=corrected,
                    )
            self._validate_calls(
                response.tool_calls,
                used_ids=used_ids,
                used_calls=used_calls,
            )
            for call in response.tool_calls:
                item, body = await self._execute_call(call, round_index=round_index)
                evidence.append(item)
                messages.append(self._provider.format_tool_result(call.id, call.name, body))
                used_ids.add(call.id)
                used_calls += 1
        raise InvestigationProtocolError("investigation round budget exceeded")
