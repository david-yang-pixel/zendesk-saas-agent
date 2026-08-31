from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, cast

from zd_agent.casework.adapters.evidence import (
    build_case_context_evidence,
    tool_evidence,
)
from zd_agent.casework.adapters.context_compaction import (
    ActiveContextDecision,
    ActiveContextPolicy,
    ContextCompactionError,
    compact_active_context,
    estimate_active_context_tokens,
)
from zd_agent.casework.adapters.model import (
    ModelOutputError,
    investigation_output_schema,
    investigation_result_from_content,
)
from zd_agent.casework.adapters.investigation_skill import (
    InvestigationSkill,
    load_investigation_skill,
)
from zd_agent.casework.adapters.token_budget import (
    CaseworkTokenBudget,
)
from zd_agent.casework.adapters.tool_loop import CaseworkToolLoop, ContinueToolLoop
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
from zd_agent.services.instruction_store import (
    SYSTEM_SECURITY_CORE,
    InstructionSnapshot,
    InstructionStore,
)
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult
from zd_agent.log import get_logger


logger = get_logger(module="casework.tool_investigator")


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
    "memory_",
    "official_docs_",
    "signoz_",
    "subagent_",
    "ticket_record_",
    "zendesk_get_",
    "zendesk_search",
)
_APPROVED_HARNESS_WRITES = frozenset(
    {
        "memory_feedback",
        "memory_propose",
        "memory_working_put",
        "subagent_cancel",
        "subagent_spawn",
    }
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
_MAX_CONCURRENT_TOOL_CALLS = 3
_BOUNDED_PAGINATION_ARGUMENTS = frozenset(
    {"limit", "max_results", "page_size", "per_page"}
)


class InvestigationProtocolError(RuntimeError):
    pass


class CustomerContextStaleError(InvestigationProtocolError):
    pass


def _approved_name(name: str) -> bool:
    return name in _APPROVED_EXACT_NAMES or name.startswith(_APPROVED_PREFIXES)


def approved_read_tools(registry: ToolRegistry) -> tuple[Tool, ...]:
    """Expose business reads plus bounded harness-state writes, fail closed."""
    return tuple(
        tool
        for tool in registry.list_tools()
        if _approved_name(tool.name)
        and tool.name not in _DISABLED_RAW_QUERY_TOOLS
        and (
            tool.name not in _SCOPED_QUERY_TOOLS
            or getattr(tool, "casework_scope_enforced", False) is True
        )
        and (not tool.is_write_operation or tool.name in _APPROVED_HARNESS_WRITES)
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
        array_value = cast(list[object], value)
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(array_value) < min_items:
            raise InvestigationProtocolError(f"{path} must contain >= {min_items} items")
        if isinstance(max_items, int) and len(array_value) > max_items:
            raise InvestigationProtocolError(f"{path} must contain <= {max_items} items")
        raw_items: object = schema.get("items")
        if raw_items is not None:
            if not isinstance(raw_items, dict):
                raise InvestigationProtocolError(f"invalid items schema at {path}")
            items = cast(dict[str, Any], raw_items)
            for index, item in enumerate(array_value):
                _validate_schema_value(item, items, path=f"{path}[{index}]")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise InvestigationProtocolError(
                f"{path} must contain >= {min_length} characters"
            )
        if isinstance(max_length, int) and len(value) > max_length:
            raise InvestigationProtocolError(
                f"{path} must contain <= {max_length} characters"
            )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise InvestigationProtocolError(f"{path} must be >= {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise InvestigationProtocolError(f"{path} must be <= {maximum}")


def _normalize_bounded_pagination_args(
    args: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Repair harmless model pagination drift before strict validation.

    Pagination counts do not change the evidence target or authorize a wider
    capability.  Clamping them to the declared tool boundary is therefore safer
    than aborting the entire investigation.  Every other schema violation stays
    fail-closed.
    """
    raw_properties: object = parameters.get("properties", {})
    if not isinstance(raw_properties, dict):
        return args
    properties = cast(dict[str, object], raw_properties)
    normalized = dict(args)
    for name in _BOUNDED_PAGINATION_ARGUMENTS & normalized.keys():
        raw_schema = properties.get(name)
        if not isinstance(raw_schema, dict):
            continue
        schema = cast(dict[str, object], raw_schema)
        if schema.get("type") != "integer":
            continue
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if not isinstance(minimum, (int, float)) and not isinstance(
            maximum, (int, float)
        ):
            continue
        raw_value = normalized[name]
        if isinstance(raw_value, bool):
            continue
        if isinstance(raw_value, int):
            value = raw_value
        elif isinstance(raw_value, str):
            try:
                value = int(raw_value.strip())
            except ValueError:
                continue
        else:
            continue
        if isinstance(minimum, (int, float)):
            value = max(value, int(minimum))
        if isinstance(maximum, (int, float)):
            value = min(value, int(maximum))
        normalized[name] = value
    return normalized


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


def _system_prompt(
    context: CaseContext,
    *,
    skill_loader: Callable[[str], InvestigationSkill] = load_investigation_skill,
    operator_extra_instructions: str = "",
) -> str:
    """Build the model prompt from the immutable core and bounded overrides."""
    skill = skill_loader(_active_question(context))
    extra = operator_extra_instructions.strip()
    layers = [SYSTEM_SECURITY_CORE]
    if extra:
        layers.extend(["## Operator extra instructions", extra])
    layers.extend(
        [
            f"ACTIVE_SERVER_SKILL={skill.name}",
            f"ACTIVE_SCENARIO={skill.scenario.value}",
            f"REQUIRED_COMPLETION_ROLES={list(skill.required_roles)!r}",
            skill.instructions,
        ]
    )
    return "\n\n".join(layers)


def _user_prompt(
    context: CaseContext,
    evidence: tuple[EvidenceItem, ...],
    *,
    skill_loader: Callable[[str], InvestigationSkill] = load_investigation_skill,
) -> str:
    skill = skill_loader(_active_question(context))
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


class BoundedToolCaseInvestigator:
    """Provider-neutral read-only investigation bounded by the session deadline."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        registry: ToolRegistry,
        session: AgentSession,
        clock: Clock,
        context_window_tokens: int = 272_000,
        context_compaction_soft_ratio: float = 0.70,
        context_compaction_hard_ratio: float = 0.88,
        context_checkpoint_max_tokens: int = 12_000,
        context_retain_recent_rounds: int = 2,
        instruction_store: InstructionStore | None = None,
        skill_loader: Callable[[str], InvestigationSkill] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._session = session
        self._clock = clock
        self._token_budget = CaseworkTokenBudget(
            session=session,
        )
        self._context_policy = ActiveContextPolicy(
            context_window_tokens=context_window_tokens,
            soft_ratio=context_compaction_soft_ratio,
            hard_ratio=context_compaction_hard_ratio,
        )
        if context_checkpoint_max_tokens < 1:
            raise ValueError("checkpoint token budget must be positive")
        if context_retain_recent_rounds < 0:
            raise ValueError("context compaction retention cannot be negative")
        self._context_checkpoint_max_tokens = context_checkpoint_max_tokens
        self._context_retain_recent_rounds = context_retain_recent_rounds
        # Freeze operator instructions once per investigator.  A save in the
        # admin console therefore affects the next investigation, never a
        # session that is already exchanging model/tool messages.
        self._instruction_snapshot: InstructionSnapshot | None = (
            instruction_store.snapshot() if instruction_store is not None else None
        )
        self._skill_loader = skill_loader
        self._tools = approved_read_tools(registry)
        self._tools_by_name = {tool.name: tool for tool in self._tools}
        self._approved_names = frozenset(tool.name for tool in self._tools)

    def _load_skill(self, question: str) -> InvestigationSkill:
        if self._skill_loader is not None:
            return self._skill_loader(question)
        return load_investigation_skill(
            question,
            snapshot=self._instruction_snapshot,
        )

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
                args=_normalize_bounded_pagination_args(
                    _arguments(call),
                    tool.parameters,
                ),
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
    ) -> tuple[tuple[EvidenceItem, ...], str]:
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
        delegated: list[EvidenceItem] = []
        raw_delegated = result.metadata.get("casework_evidence_items", [])
        if call.name == "subagent_wait" and isinstance(raw_delegated, list):
            for raw_item in cast(list[object], raw_delegated):
                if not isinstance(raw_item, dict):
                    continue
                candidate = EvidenceItem.model_validate(raw_item)
                if not candidate.evidence_id.startswith("subagent:"):
                    continue
                if candidate.source.startswith(("subagent_", "memory_")):
                    continue
                delegated.append(candidate)
        return (item, *delegated), _tool_result_body(call.id, result)

    async def _execute_calls(
        self,
        calls: list[ToolCallRequest],
        *,
        validated_args: dict[str, dict[str, Any]],
        round_index: int,
    ) -> list[tuple[tuple[EvidenceItem, ...], str]]:
        """Execute one model batch concurrently only when every read is safe."""
        if len(calls) > 1 and all(
            self._tools_by_name[call.name].concurrency_safe for call in calls
        ):
            semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TOOL_CALLS)

            async def execute_limited(
                call: ToolCallRequest,
            ) -> tuple[tuple[EvidenceItem, ...], str]:
                async with semaphore:
                    return await self._execute_call(
                        call,
                        args=validated_args[call.id],
                        round_index=round_index,
                    )

            return list(
                await asyncio.gather(*(execute_limited(call) for call in calls))
            )

        results: list[tuple[tuple[EvidenceItem, ...], str]] = []
        for call in calls:
            results.append(
                await self._execute_call(
                    call,
                    args=validated_args[call.id],
                    round_index=round_index,
                )
            )
        return results

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
        self._token_budget.record(response)

    def _ensure_tokens_available(self) -> None:
        self._token_budget.ensure_available()

    def _ensure_customer_context_current(self) -> None:
        if self._session.customer_context_dirty:
            raise CustomerContextStaleError(
                "stale_context: customer added a message during investigation"
            )

    async def _prepare_active_context(
        self,
        *,
        context: CaseContext,
        evidence: list[EvidenceItem],
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None,
    ) -> list[dict[str, Any]]:
        self._ensure_customer_context_current()
        estimated = estimate_active_context_tokens(messages, tools)
        self._session.active_context_tokens = estimated
        decision = self._context_policy.decision(estimated)
        if decision is ActiveContextDecision.CONTINUE:
            return messages

        has_tool_history = any(
            message.get("role") == "assistant" and message.get("tool_calls")
            for message in messages
        )
        if not has_tool_history:
            if decision is ActiveContextDecision.HARD_LIMIT:
                raise InvestigationProtocolError(
                    "active context hard limit reached before investigation tools"
                )
            return messages

        trigger = decision.value
        logger.info(
            "context_compaction_started",
            ticket_id=context.ticket.ticket_id,
            session_id=self._session.session_id,
            generation=context.ticket.generation,
            trigger=trigger,
            tokens_before=estimated,
            soft_limit=self._context_policy.soft_limit,
            hard_limit=self._context_policy.hard_limit,
        )
        try:
            compacted = await compact_active_context(
                provider=self._provider,
                context=context,
                evidence=tuple(evidence),
                messages=messages,
                tools=tools,
                created_at=self._clock.now(),
                max_checkpoint_tokens=self._context_checkpoint_max_tokens,
                retain_recent_rounds=self._context_retain_recent_rounds,
                on_response=self._record_tokens,
            )
        except ContextCompactionError as error:
            logger.error(
                "context_compaction_failed",
                ticket_id=context.ticket.ticket_id,
                session_id=self._session.session_id,
                generation=context.ticket.generation,
                trigger=trigger,
                error_type=type(error).__name__,
            )
            raise InvestigationProtocolError(str(error)) from error
        self._ensure_customer_context_current()
        if compacted.tokens_after >= self._context_policy.hard_limit:
            raise InvestigationProtocolError(
                "active context remains above hard limit after compaction"
            )
        self._session.log_context_compaction(
            trigger=trigger,
            tokens_before=compacted.tokens_before,
            tokens_after=compacted.tokens_after,
            checkpoint=compacted.checkpoint.model_dump(mode="json"),
        )
        logger.info(
            "context_compaction_completed",
            ticket_id=context.ticket.ticket_id,
            session_id=self._session.session_id,
            generation=context.ticket.generation,
            trigger=trigger,
            tokens_before=compacted.tokens_before,
            tokens_after=compacted.tokens_after,
            compaction_count=self._session.context_compaction_count,
        )
        return compacted.messages

    async def investigate(self, context: CaseContext) -> InvestigationResult:
        context_evidence = build_case_context_evidence(context)
        evidence = list(context_evidence)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _system_prompt(
                    context,
                    skill_loader=self._load_skill,
                    operator_extra_instructions=(
                        self._instruction_snapshot.operator_extra_instructions
                        if self._instruction_snapshot is not None
                        else ""
                    ),
                ),
            },
            {
                "role": "user",
                "content": _user_prompt(
                    context,
                    context_evidence,
                    skill_loader=self._load_skill,
                ),
            },
        ]
        schemas = _tool_schemas(self._tools)

        async def prepare_messages(
            active_messages: list[dict[str, Any]],
            active_schemas: list[ToolSchema] | None,
        ) -> list[dict[str, Any]]:
            self._ensure_tokens_available()
            return await self._prepare_active_context(
                context=context,
                evidence=evidence,
                messages=active_messages,
                tools=active_schemas,
            )

        def validate_calls(
            calls: list[ToolCallRequest],
            used_ids: set[str],
        ) -> dict[str, dict[str, Any]]:
            return self._validate_calls(
                calls,
                context=context,
                used_ids=used_ids,
            )

        async def execute_calls(
            calls: list[ToolCallRequest],
            validated_args: dict[str, dict[str, Any]],
            round_index: int,
        ) -> list[tuple[tuple[EvidenceItem, ...], str]]:
            return await self._execute_calls(
                calls,
                validated_args=validated_args,
                round_index=round_index,
            )

        def format_call_result(
            call: ToolCallRequest,
            result: tuple[tuple[EvidenceItem, ...], str],
        ) -> dict[str, Any]:
            items, body = result
            existing_ids = {item.evidence_id for item in evidence}
            for item in items:
                if item.evidence_id in existing_ids:
                    raise InvestigationProtocolError(
                        f"duplicate evidence ID from tool result: {item.evidence_id}"
                    )
                evidence.append(item)
                existing_ids.add(item.evidence_id)
            return self._provider.format_tool_result(call.id, call.name, body)

        async def handle_terminal(
            active_messages: list[dict[str, Any]],
            content: str,
        ) -> InvestigationResult | ContinueToolLoop:
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
                    return ContinueToolLoop(
                        "The investigation is incomplete and cannot end yet. "
                        "Continue with authoritative tools. Missing material "
                        "claim roles: "
                        + ", ".join(role.value for role in missing)
                    )
                return result
            except ModelOutputError as error:
                corrected = await self._correct_terminal_output(
                    messages=active_messages,
                    error=error,
                )
                return investigation_result_from_content(
                    context=context,
                    evidence=tuple(evidence),
                    content=corrected,
                )

        loop = CaseworkToolLoop[
            InvestigationResult, tuple[tuple[EvidenceItem, ...], str]
        ](
            provider=self._provider,
            schemas=schemas,
            prepare_messages=prepare_messages,
            validate_calls=validate_calls,
            execute_calls=execute_calls,
            format_call_result=format_call_result,
            handle_terminal=handle_terminal,
            record_response=self._record_tokens,
            assistant_message=_assistant_message,
            temperature=0.0,
        )
        return await loop.run(messages)
