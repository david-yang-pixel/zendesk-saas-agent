from __future__ import annotations

import json
from typing import Any, cast

from zd_agent.casework.adapters.evidence import (
    build_case_context_evidence,
    tool_evidence,
)
from zd_agent.casework.adapters.model import (
    ModelOutputError,
    investigation_output_schema,
    investigation_result_from_content,
)
from zd_agent.casework.adapters.investigation_skill import load_investigation_skill
from zd_agent.casework.adapters.token_budget import (
    CaseworkTokenBudget,
    CaseworkTokenBudgetExceeded,
)
from zd_agent.casework.domain.enums import (
    EvidenceAuthority,
    EvidenceResultStatus,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    CaseContext,
    Claim,
    EvidenceItem,
    InvestigationResult,
)
from zd_agent.casework.ports.readers import Clock
from zd_agent.casework.policies.investigation_completion import (
    missing_completion_roles,
)
from zd_agent.runtime.llm.types import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


_APPROVED_EXACT_NAMES = frozenset(
    {
        "inspector_exec",
        "document_indexing_status",
        "list_repo_tags",
        "list_repos",
        "saas_deployed_version",
        "signoz_query",
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
_DISABLED_RAW_QUERY_TOOLS: frozenset[str] = frozenset()
_SCOPED_QUERY_TOOLS = frozenset({"sql_query", "signoz_query"})
_SAFE_UNRESOLVED_ZENDESK_TOOLS = frozenset(
    {"zendesk_get_article", "zendesk_search_articles"},
)
_INSPECTOR_PRIMARY_ARGUMENTS = frozenset(
    {
        "email",
        "account_id",
        "tenant_id",
        "app_id",
        "dataset_id",
        "document_id",
        "conversation_id",
        "workflow_id",
        "workflow_run_id",
        "id",
    },
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
        and tool.name not in _DISABLED_RAW_QUERY_TOOLS
        and (
            tool.name not in _SCOPED_QUERY_TOOLS
            or getattr(tool, "casework_scope_enforced", False) is True
        )
        and not tool.is_write_operation
        and not tool.requires_sandbox
    )


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in schema.items():
        if isinstance(value, dict):
            normalized[key] = _strict_schema(cast(dict[str, Any], value))
        elif isinstance(value, list):
            items = cast(list[object], value)
            normalized[key] = [
                _strict_schema(cast(dict[str, Any], item))
                if isinstance(item, dict)
                else item
                for item in items
            ]
        else:
            normalized[key] = value
    if normalized.get("type") == "object" or "properties" in normalized:
        normalized["additionalProperties"] = False
    return normalized


def _tool_schemas(tools: tuple[Tool, ...]) -> list[ToolSchema]:
    return [
        ToolSchema(
            name=tool.name,
            description=tool.description,
            parameters=_strict_schema(tool.parameters),
        )
        for tool in tools
    ]


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    response_raw: object = response.raw
    raw = cast(dict[str, Any], response_raw) if isinstance(response_raw, dict) else {}
    message: dict[str, Any] = dict(raw)
    message["role"] = "assistant"
    message["content"] = response.content
    message.pop("tool_calls", None)
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


def _reject_non_json_constant(value: str) -> object:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _arguments(call: ToolCallRequest) -> dict[str, Any]:
    try:
        parsed: object = (
            json.loads(call.arguments, parse_constant=_reject_non_json_constant)
            if call.arguments
            else {}
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise InvestigationProtocolError(
            f"invalid arguments for {call.name}: {error}",
        ) from error
    if not isinstance(parsed, dict):
        raise InvestigationProtocolError(
            f"arguments for {call.name} must be a JSON object",
        )
    return cast(dict[str, Any], parsed)


def _matches_json_type(value: object, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _schema_types(schema: dict[str, Any], *, path: str) -> tuple[str, ...]:
    raw_types: object = schema.get("type")
    if raw_types is None:
        return ()
    if isinstance(raw_types, str):
        types = (raw_types,)
    elif isinstance(raw_types, list) and all(
        isinstance(item, str) for item in cast(list[object], raw_types)
    ):
        types = tuple(cast(list[str], raw_types))
    else:
        raise InvestigationProtocolError(f"invalid tool schema type at {path}")
    supported = {
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
        "null",
    }
    if unknown := set(types) - supported:
        raise InvestigationProtocolError(
            f"unsupported tool schema type at {path}: {', '.join(sorted(unknown))}",
        )
    return types


def _enum_contains(enum: list[object], value: object) -> bool:
    return any(
        type(candidate) is type(value) and candidate == value for candidate in enum
    )


def _validate_schema_value(
    value: object,
    schema: dict[str, Any],
    *,
    path: str,
) -> None:
    types = _schema_types(schema, path=path)
    if types and not any(_matches_json_type(value, expected) for expected in types):
        raise InvestigationProtocolError(f"{path} must be {' or '.join(types)}")

    raw_enum: object = schema.get("enum")
    if raw_enum is not None:
        if not isinstance(raw_enum, list):
            raise InvestigationProtocolError(f"invalid tool schema enum at {path}")
        enum = cast(list[object], raw_enum)
        if not _enum_contains(enum, value):
            raise InvestigationProtocolError(f"{path} must be one of {enum!r}")

    if isinstance(value, dict):
        object_value = cast(dict[str, object], value)
        raw_properties: object = schema.get("properties", {})
        if not isinstance(raw_properties, dict):
            raise InvestigationProtocolError(
                f"invalid tool schema properties at {path}",
            )
        untyped_properties = cast(dict[object, object], raw_properties)
        if not all(
            isinstance(name, str) and isinstance(child, dict)
            for name, child in untyped_properties.items()
        ):
            raise InvestigationProtocolError(
                f"invalid tool schema properties at {path}",
            )
        properties = cast(dict[str, dict[str, Any]], raw_properties)
        raw_required: object = schema.get("required", [])
        if not isinstance(raw_required, list) or not all(
            isinstance(name, str) for name in cast(list[object], raw_required)
        ):
            raise InvestigationProtocolError(f"invalid required schema at {path}")
        required = set(cast(list[str], raw_required))
        missing = sorted(required - set(object_value))
        if missing:
            raise InvestigationProtocolError(
                f"{path} is missing required properties: {', '.join(missing)}",
            )
        unknown = sorted(set(object_value) - set(properties))
        if unknown:
            raise InvestigationProtocolError(
                f"{path} has unknown properties: {', '.join(unknown)}",
            )
        for name, child_value in object_value.items():
            _validate_schema_value(
                child_value,
                properties[name],
                path=f"{path}.{name}",
            )

    if isinstance(value, list):
        raw_items: object = schema.get("items")
        if raw_items is not None:
            if not isinstance(raw_items, dict):
                raise InvestigationProtocolError(f"invalid items schema at {path}")
            items = cast(dict[str, Any], raw_items)
            for index, item in enumerate(cast(list[object], value)):
                _validate_schema_value(item, items, path=f"{path}[{index}]")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise InvestigationProtocolError(f"{path} must be >= {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise InvestigationProtocolError(f"{path} must be <= {maximum}")


def _is_tenant_data_tool(name: str) -> bool:
    if name in {"document_indexing_status", "inspector_exec", "sql_query"}:
        return True
    if name.startswith(("signoz_", "ticket_record_")):
        return True
    return name.startswith("zendesk_") and name not in _SAFE_UNRESOLVED_ZENDESK_TOOLS


def _deny_current_case_capability(tool_name: str, detail: str) -> None:
    raise InvestigationProtocolError(
        f"{tool_name} is not permitted by the current case capability: {detail}",
    )


def _normalized_string(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    return value.strip() if isinstance(value, str) else ""


def _validate_explicit_tenant_scope(
    *,
    tool_name: str,
    args: dict[str, Any],
    resolved_tenant_id: str,
) -> None:
    for name in ("tenant_id", "workspace_id", "tenant"):
        value = _normalized_string(args, name)
        if value and value.casefold() != resolved_tenant_id.casefold():
            raise InvestigationProtocolError(
                f"{tool_name}.{name} is outside the resolved tenant",
            )


def _validate_inspector_capability(
    *,
    args: dict[str, Any],
    context: CaseContext,
) -> None:
    primary = tuple(
        name for name in _INSPECTOR_PRIMARY_ARGUMENTS if _normalized_string(args, name)
    )
    if len(primary) != 1:
        _deny_current_case_capability(
            "inspector_exec",
            "exactly one trusted primary lookup is required",
        )
    primary_name = primary[0]
    if primary_name == "tenant_id":
        return
    if primary_name == "email":
        requester = context.ticket.requester_email.strip().casefold()
        email = _normalized_string(args, "email").casefold()
        if not requester or email != requester:
            _deny_current_case_capability(
                "inspector_exec",
                "email lookup must equal the current ticket requester",
            )
        if _normalized_string(args, "section").casefold() != "tenants":
            _deny_current_case_capability(
                "inspector_exec",
                "requester email lookup is limited to section=tenants",
            )
        return
    _deny_current_case_capability(
        "inspector_exec",
        f"{primary_name} is not a trusted identifier in the current case",
    )


def _validate_signoz_capability(
    *,
    tool_name: str,
    args: dict[str, Any],
) -> None:
    if tool_name == "signoz_traceback" and _normalized_string(args, "id"):
        if _normalized_string(args, "tenant"):
            raise InvestigationProtocolError(
                "signoz_traceback cannot combine id and tenant because id lookup is "
                "not tenant-scoped; it is not permitted by the current case capability",
            )
        _deny_current_case_capability(
            tool_name,
            "exact log ID lookup is not tenant-scoped",
        )
    if not _normalized_string(args, "tenant"):
        _deny_current_case_capability(
            tool_name,
            "SigNoz reads require the resolved tenant filter at execution",
        )


def _validate_zendesk_capability(
    *,
    tool_name: str,
    args: dict[str, Any],
    current_ticket_id: int,
) -> None:
    if tool_name in _SAFE_UNRESOLVED_ZENDESK_TOOLS:
        return
    if tool_name == "zendesk_get_ticket":
        if args.get("ticket_id") != current_ticket_id:
            _deny_current_case_capability(
                tool_name,
                "ticket_id must equal the current ticket",
            )
        return
    _deny_current_case_capability(
        tool_name,
        "cross-customer Zendesk user and search reads are not available",
    )


def _validate_ticket_record_capability(
    *,
    tool_name: str,
    args: dict[str, Any],
    current_ticket_id: int,
) -> None:
    if tool_name == "ticket_record_read" and _normalized_string(
        args, "ticket_id"
    ) == str(current_ticket_id):
        return
    _deny_current_case_capability(
        tool_name,
        "historical records are limited to the current ticket",
    )


def _validate_tool_capability(
    *,
    tool_name: str,
    args: dict[str, Any],
    context: CaseContext,
) -> None:
    identity = context.identity
    if identity.status is not ResolutionStatus.RESOLVED and _is_tenant_data_tool(
        tool_name,
    ):
        raise InvestigationProtocolError(
            f"tenant-data tool requires resolved identity: {tool_name}",
        )
    if identity.status is ResolutionStatus.RESOLVED:
        _validate_explicit_tenant_scope(
            tool_name=tool_name,
            args=args,
            resolved_tenant_id=identity.selected_tenant_id,
        )
    if tool_name == "inspector_exec":
        _validate_inspector_capability(args=args, context=context)
    elif tool_name.startswith("signoz_"):
        _validate_signoz_capability(tool_name=tool_name, args=args)
    elif tool_name.startswith("zendesk_"):
        _validate_zendesk_capability(
            tool_name=tool_name,
            args=args,
            current_ticket_id=context.ticket.ticket_id,
        )
    elif tool_name.startswith("ticket_record_"):
        _validate_ticket_record_capability(
            tool_name=tool_name,
            args=args,
            current_ticket_id=context.ticket.ticket_id,
        )


def _bind_trusted_tool_scope(
    *,
    tool_name: str,
    args: dict[str, Any],
    parameters: dict[str, Any],
    context: CaseContext,
) -> dict[str, Any]:
    """Inject scope resolved by code instead of asking the model to reproduce it."""
    if context.identity.status is not ResolutionStatus.RESOLVED:
        return args
    raw_properties: object = parameters.get("properties", {})
    if not isinstance(raw_properties, dict):
        return args
    if tool_name.startswith("signoz_") and "tenant" in raw_properties:
        return (
            args
            if _normalized_string(args, "tenant")
            else {**args, "tenant": context.identity.selected_tenant_id}
        )
    if (
        tool_name in {"document_indexing_status", "sql_query"}
        and "tenant_id" in raw_properties
    ):
        return (
            args
            if _normalized_string(args, "tenant_id")
            else {**args, "tenant_id": context.identity.selected_tenant_id}
        )
    return args


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
            "active_question": (
                context.ticket.active_question or context.ticket.questions[-1]
            ),
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


def _active_question(context: CaseContext) -> str:
    return context.ticket.active_question or context.ticket.questions[-1]


def _completion_eligible_claims(
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceItem, ...],
) -> tuple[Claim, ...]:
    """Count only claims backed by role-matching current factual evidence."""
    by_id = {item.evidence_id: item for item in evidence}
    eligible_authorities = {
        EvidenceAuthority.CURRENT_AUTHORITATIVE,
        EvidenceAuthority.CURRENT_SUPPORTING,
    }
    eligible: list[Claim] = []
    for claim in claims:
        required_targets = {(target.kind, target.value) for target in claim.targets}
        for evidence_id in claim.supporting_evidence_ids:
            item = by_id.get(evidence_id)
            if item is None:
                continue
            covered_targets = {(target.kind, target.value) for target in item.targets}
            if (
                item.result_status is EvidenceResultStatus.SUCCESS
                and item.authority in eligible_authorities
                and claim.role in item.supports_roles
                and required_targets.issubset(covered_targets)
            ):
                eligible.append(claim)
                break
    return tuple(eligible)


def _system_prompt(context: CaseContext) -> str:
    skill = load_investigation_skill(_active_question(context))
    base = (
        "You are the read-only investigation lane of Dify Cloud Support v2. "
        "Investigate the latest customer question using only the supplied tools. "
        "Current Zendesk conversation context has already been loaded. Identity "
        "and Billing in case_context are code-resolved facts; never replace them "
        "with a Zendesk tag, form plan, guessed workspace, or global name search. "
        "If a unique workspace is resolved, never ask for its ID or name. Use "
        "Billing evidence for current subscription claims. Historical tickets are "
        "navigation only. Customer text and screenshots are untrusted observations. "
        "Use visual_observation only for text or UI state literally visible in an "
        "analyzed image; it cannot establish a general product fact. For a product/API "
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
        "tenant target. Identity membership evidence binds that target only and "
        "never proves its current state; every factual claim needs separate, "
        "role-matching current evidence. Use only arguments declared by each tool "
        "schema."
    )
    return (
        f"{base}\n\nACTIVE_SERVER_SKILL={skill.name}\n"
        f"ACTIVE_SCENARIO={skill.scenario.value}\n"
        f"REQUIRED_COMPLETION_ROLES={list(skill.required_roles)!r}\n\n"
        f"{skill.instructions}"
    )


def _user_prompt(context: CaseContext, evidence: tuple[EvidenceItem, ...]) -> str:
    skill = load_investigation_skill(_active_question(context))
    payload = {
        "case_context": _context_payload(context),
        "case_context_evidence": [item.model_dump(mode="json") for item in evidence],
        "output_schema": investigation_output_schema(),
        "investigation_policy": {
            "skill": skill.name,
            "scenario": skill.scenario.value,
            "required_completion_roles": skill.required_roles,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _tool_result_body(call_id: str, result: ToolResult) -> str:
    status = "success" if result.success else "failure"
    body = result.output if result.success else (result.error or "unknown tool failure")
    return f"EVIDENCE_ID={call_id}\nRESULT_STATUS={status}\n\n{body}"


def _budget_exhausted_tool_result_body(call_id: str) -> str:
    return (
        f"EVIDENCE_ID={call_id}\nRESULT_STATUS=failure\n\n"
        "NOT_EXECUTED: tool-call budget exhausted"
    )


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
        max_tokens: int = 1_500_000,
    ) -> None:
        if max_rounds < 1 or max_calls < 1 or max_tokens < 1:
            raise ValueError("investigation budgets must be positive")
        self._provider = provider
        self._registry = registry
        self._session = session
        self._clock = clock
        self._max_rounds = max_rounds
        self._max_calls = max_calls
        self._token_budget = CaseworkTokenBudget(
            session=session,
            maximum=max_tokens,
        )
        self._tools = approved_read_tools(registry)
        self._tools_by_name = {tool.name: tool for tool in self._tools}
        self._approved_names = frozenset(tool.name for tool in self._tools)

    def _validate_calls(
        self,
        calls: list[ToolCallRequest],
        *,
        context: CaseContext,
        used_ids: set[str],
    ) -> dict[str, dict[str, Any]]:
        ids = [call.id for call in calls]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise InvestigationProtocolError(
                "tool call IDs must be non-empty and unique"
            )
        if set(ids) & used_ids:
            raise InvestigationProtocolError("tool call ID was reused")
        if rejected := next(
            (call.name for call in calls if call.name not in self._approved_names),
            "",
        ):
            raise InvestigationProtocolError(
                f"tool is not approved for v2 read-only investigation: {rejected}",
            )
        validated: dict[str, dict[str, Any]] = {}
        for call in calls:
            tool = self._tools_by_name[call.name]
            args = _bind_trusted_tool_scope(
                tool_name=call.name,
                args=_arguments(call),
                parameters=tool.parameters,
                context=context,
            )
            _validate_schema_value(
                args,
                tool.parameters,
                path=f"arguments for {call.name}",
            )
            _validate_tool_capability(
                tool_name=call.name,
                args=args,
                context=context,
            )
            validated[call.id] = args
        return validated

    async def _execute_call(
        self,
        call: ToolCallRequest,
        *,
        args: dict[str, Any],
        round_index: int,
    ) -> tuple[EvidenceItem, str]:
        tool = self._tools_by_name[call.name]
        result = await self._registry.execute(
            call.name,
            json.dumps(args, ensure_ascii=False, separators=(",", ":")),
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
            allowed_target_arguments=frozenset(
                str(name)
                for name in cast(
                    dict[str, Any],
                    tool.parameters.get("properties", {}),
                )
            ),
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
        self._ensure_tokens_available()
        response = await self._provider.chat(messages, tools=None, temperature=0.0)
        self._record_tokens(response)
        if response.tool_calls:
            raise InvestigationProtocolError(
                "model attempted a tool call during terminal JSON correction",
            )
        return response.content or ""

    def _record_tokens(self, response: LLMResponse) -> None:
        try:
            self._token_budget.record(response)
        except CaseworkTokenBudgetExceeded as error:
            raise InvestigationProtocolError(
                "investigation model token budget exceeded",
            ) from error

    def _ensure_tokens_available(self) -> None:
        try:
            self._token_budget.ensure_available()
        except CaseworkTokenBudgetExceeded as error:
            raise InvestigationProtocolError(
                "investigation model token budget exhausted",
            ) from error

    async def _terminal_result(
        self,
        *,
        context: CaseContext,
        evidence: list[EvidenceItem],
        messages: list[dict[str, Any]],
        instruction: str,
    ) -> InvestigationResult:
        messages.append({"role": "user", "content": instruction})
        self._ensure_tokens_available()
        response = await self._provider.chat(messages, tools=None, temperature=0.0)
        self._record_tokens(response)
        if response.tool_calls:
            raise InvestigationProtocolError(
                "model attempted a tool call after investigation budget exhaustion",
            )
        messages.append(_assistant_message(response))
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

    async def investigate(self, context: CaseContext) -> InvestigationResult:
        context_evidence = build_case_context_evidence(context)
        evidence = list(context_evidence)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(context)},
            {"role": "user", "content": _user_prompt(context, context_evidence)},
        ]
        schemas = _tool_schemas(self._tools)
        used_ids: set[str] = set()
        used_calls = 0
        for round_index in range(1, self._max_rounds + 1):
            self._ensure_tokens_available()
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
                    result = investigation_result_from_content(
                        context=context,
                        evidence=tuple(evidence),
                        content=content,
                    )
                    missing = missing_completion_roles(
                        result.proposal.question,
                        _completion_eligible_claims(
                            result.proposal.proposed_claims,
                            tuple(evidence),
                        ),
                    )
                    if (
                        missing
                        and result.proposal.answer_status.value
                        in {"supported", "bounded"}
                        and not result.proposal.proposed_customer_fields
                        and result.proposal.action_proposal is None
                    ):
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "The investigation is incomplete and cannot end yet. "
                                    "Continue with authoritative tools. Missing material "
                                    "claim roles: "
                                    + ", ".join(role.value for role in missing)
                                ),
                            },
                        )
                        continue
                    return result
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
            validated_args = self._validate_calls(
                response.tool_calls,
                context=context,
                used_ids=used_ids,
            )
            if used_calls + len(response.tool_calls) > self._max_calls:
                for call in response.tool_calls:
                    messages.append(
                        self._provider.format_tool_result(
                            call.id,
                            call.name,
                            _budget_exhausted_tool_result_body(call.id),
                        )
                    )
                self._session.round_count = max(
                    self._session.round_count,
                    round_index,
                )
                return await self._terminal_result(
                    context=context,
                    evidence=evidence,
                    messages=messages,
                    instruction=(
                        "The investigation tool-call budget is exhausted. Do not "
                        "call tools. Return only the required terminal JSON using "
                        "the evidence already collected. Mark unsupported facts "
                        "unknown or insufficient."
                    ),
                )
            for call in response.tool_calls:
                item, body = await self._execute_call(
                    call,
                    args=validated_args[call.id],
                    round_index=round_index,
                )
                evidence.append(item)
                messages.append(
                    self._provider.format_tool_result(call.id, call.name, body)
                )
                used_ids.add(call.id)
                used_calls += 1
        return await self._terminal_result(
            context=context,
            evidence=evidence,
            messages=messages,
            instruction=(
                "The investigation round budget is exhausted. Do not call tools. "
                "Return only the required terminal JSON using the evidence already "
                "collected. Mark unsupported facts unknown or insufficient."
            ),
        )
