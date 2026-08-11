from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from zd_agent.casework.domain.enums import (
    ClaimRole,
    EvidenceAuthority,
    EvidenceResultStatus,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import CaseContext, EvidenceItem, TargetReference
from zd_agent.session.agent_session import AgentSession


@dataclass(frozen=True, slots=True)
class LegacyCaseMaterial:
    evidence: tuple[EvidenceItem, ...]
    legacy_reply_type: str = ""
    legacy_reply_body: str = ""
    legacy_analysis: str = ""


_TARGET_ARGUMENTS = (
    "tenant_id",
    "workspace_id",
    "account_id",
    "app_id",
    "dataset_id",
    "document_id",
    "conversation_id",
    "workflow_id",
    "workflow_run_id",
    "message_id",
    "invocation_id",
)


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, str]:
    results: dict[str, str] = {}
    for message in messages:
        if message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            content = message.get("content")
            if isinstance(call_id, str) and isinstance(content, str):
                results[call_id] = content
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call_id = block.get("tool_use_id")
            body = block.get("content")
            if isinstance(call_id, str) and isinstance(body, str):
                results[call_id] = body
    return results


def _without_evidence_banner(content: str) -> str:
    if not content.startswith("EVIDENCE_CALL_ID="):
        return content
    _, separator, remainder = content.partition("\n\n")
    return remainder if separator else content


def _targets(args: dict[str, Any], ticket_id: int) -> tuple[TargetReference, ...]:
    targets = [TargetReference(kind="ticket_id", value=str(ticket_id))]
    for name in _TARGET_ARGUMENTS:
        value = args.get(name)
        if isinstance(value, (str, int)) and str(value).strip():
            targets.append(TargetReference(kind=name, value=str(value).strip()))
    deduplicated = {(target.kind, target.value): target for target in targets}
    return tuple(deduplicated.values())


def _evidence_contract(
    tool_name: str,
) -> tuple[EvidenceAuthority, tuple[ClaimRole, ...]]:
    lowered = tool_name.casefold()
    if lowered.startswith("ticket_record_"):
        return EvidenceAuthority.HISTORICAL_GUIDANCE, (ClaimRole.PRODUCT_FACT,)
    if any(value in lowered for value in ("source", "github", "git_")):
        return EvidenceAuthority.CURRENT_SUPPORTING, (
            ClaimRole.PRODUCT_FACT,
            ClaimRole.ROOT_CAUSE,
            ClaimRole.LIMITATION,
        )
    if any(value in lowered for value in ("knowledge", "official_docs", "feishu")):
        return EvidenceAuthority.CURRENT_SUPPORTING, (
            ClaimRole.PRODUCT_FACT,
            ClaimRole.LIMITATION,
        )
    if "signoz" in lowered:
        return EvidenceAuthority.CURRENT_AUTHORITATIVE, (
            ClaimRole.CURRENT_STATE,
            ClaimRole.FAILING_BOUNDARY,
            ClaimRole.IMPACT,
            ClaimRole.RECOVERY,
        )
    if lowered in {"inspector_exec", "sql_query"}:
        return EvidenceAuthority.CURRENT_AUTHORITATIVE, (
            ClaimRole.PRODUCT_FACT,
            ClaimRole.CURRENT_STATE,
            ClaimRole.FAILING_BOUNDARY,
            ClaimRole.IMPACT,
            ClaimRole.RECOVERY,
        )
    return EvidenceAuthority.CURRENT_SUPPORTING, (
        ClaimRole.PRODUCT_FACT,
        ClaimRole.CURRENT_STATE,
    )


def _legacy_tool_evidence(
    *,
    session: AgentSession,
    read_tool_names: frozenset[str],
    observed_at: datetime,
) -> tuple[EvidenceItem, ...]:
    full_results = _tool_results(session.messages)
    items: list[EvidenceItem] = []
    for raw in session.tool_call_log:
        call_id = raw.get("call_id")
        tool_name = raw.get("tool")
        if (
            raw.get("success") is not True
            or not isinstance(call_id, str)
            or not call_id
            or not isinstance(tool_name, str)
            or tool_name not in read_tool_names
        ):
            continue
        full_result = full_results.get(call_id)
        if not full_result:
            continue
        raw_args = raw.get("args")
        args = cast(dict[str, Any], raw_args) if isinstance(raw_args, dict) else {}
        authority, roles = _evidence_contract(tool_name)
        items.append(
            EvidenceItem(
                evidence_id=call_id,
                call_id=call_id,
                source=tool_name,
                authority=authority,
                result_status=EvidenceResultStatus.SUCCESS,
                targets=_targets(args, session.ticket_id),
                observed_at=observed_at,
                supports_roles=roles,
                payload=_without_evidence_banner(full_result),
                redacted=False,
            ),
        )
    return tuple(items)


def _context_targets(context: CaseContext) -> tuple[TargetReference, ...]:
    targets = [
        TargetReference(kind="ticket_id", value=str(context.ticket.ticket_id)),
    ]
    if context.identity.status is ResolutionStatus.RESOLVED:
        targets.append(
            TargetReference(kind="tenant_id", value=context.identity.selected_tenant_id),
        )
    return tuple(targets)


def _context_evidence(context: CaseContext) -> tuple[EvidenceItem, ...]:
    targets = _context_targets(context)
    items = {
        evidence_id: EvidenceItem(
            evidence_id=evidence_id,
            call_id=evidence_id,
            source="case_context.identity",
            authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
            result_status=EvidenceResultStatus.SUCCESS,
            targets=targets,
            observed_at=context.built_at,
            supports_roles=(ClaimRole.CURRENT_STATE,),
            payload={
                "status": context.identity.status.value,
                "method": context.identity.method.value,
                "candidate_count": len(context.identity.candidates),
            },
        )
        for evidence_id in context.identity.evidence_ids
    }
    billing = context.billing.snapshot if context.billing else None
    if billing is not None:
        billing_only_ids = set(billing.evidence_ids) - set(context.identity.evidence_ids)
        for evidence_id in billing_only_ids:
            items[evidence_id] = EvidenceItem(
                evidence_id=evidence_id,
                call_id=evidence_id,
                source="case_context.billing",
                authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
                result_status=EvidenceResultStatus.SUCCESS,
                targets=targets,
                observed_at=billing.observed_at,
                supports_roles=(ClaimRole.PRODUCT_FACT, ClaimRole.CURRENT_STATE),
                payload={
                    "plan": billing.plan,
                    "subscription_status": billing.subscription_status,
                    "interval": billing.interval,
                    "stripe_status": billing.stripe_status,
                    "entitlement_status": billing.entitlement_status,
                    "cancellation_status": billing.cancellation_status,
                    "renewal_status": billing.renewal_status,
                },
            )
    return tuple(items.values())


def _legacy_reply(session: AgentSession) -> tuple[str, str]:
    for raw in reversed(session.tool_call_log):
        if raw.get("tool") != "zendesk_public_reply" or raw.get("success") is not True:
            continue
        raw_args = raw.get("args")
        if not isinstance(raw_args, dict):
            continue
        body = raw_args.get("body")
        reply_type = raw_args.get("reply_type")
        return (
            body if isinstance(body, str) else "",
            reply_type if isinstance(reply_type, str) else "",
        )
    return session.customer_reply_draft or session.reply_draft, session.public_reply_type


def build_legacy_case_material(
    *,
    session: AgentSession,
    context: CaseContext,
    read_tool_names: frozenset[str],
    observed_at: datetime,
) -> LegacyCaseMaterial:
    tool_evidence = _legacy_tool_evidence(
        session=session,
        read_tool_names=read_tool_names,
        observed_at=observed_at,
    )
    combined = {item.evidence_id: item for item in _context_evidence(context)}
    combined.update({item.evidence_id: item for item in tool_evidence})
    body, reply_type = _legacy_reply(session)
    return LegacyCaseMaterial(
        evidence=tuple(combined.values()),
        legacy_reply_type=reply_type,
        legacy_reply_body=body,
        legacy_analysis=session.analysis,
    )
