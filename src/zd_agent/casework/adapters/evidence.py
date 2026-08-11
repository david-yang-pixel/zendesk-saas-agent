from __future__ import annotations

from datetime import datetime
from typing import Any

from zd_agent.casework.domain.enums import (
    ClaimRole,
    EvidenceAuthority,
    EvidenceResultStatus,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    CaseContext,
    EvidenceItem,
    TargetReference,
)


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
    "traceId",
)


def evidence_contract(
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
    if any(value in lowered for value in ("knowledge", "official_docs")):
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


def evidence_targets(
    args: dict[str, Any],
    *,
    ticket_id: int,
) -> tuple[TargetReference, ...]:
    targets = [TargetReference(kind="ticket_id", value=str(ticket_id))]
    for name in _TARGET_ARGUMENTS:
        value = args.get(name)
        if isinstance(value, (str, int)) and str(value).strip():
            kind = "trace_id" if name == "traceId" else name
            targets.append(TargetReference(kind=kind, value=str(value).strip()))
    tenant = args.get("tenant")
    if isinstance(tenant, str) and tenant.strip():
        targets.append(TargetReference(kind="tenant_id", value=tenant.strip()))
    deduplicated = {(target.kind, target.value): target for target in targets}
    return tuple(deduplicated.values())


def tool_evidence(
    *,
    call_id: str,
    tool_name: str,
    args: dict[str, Any],
    ticket_id: int,
    observed_at: datetime,
    success: bool,
    output: str,
    error: str,
) -> EvidenceItem:
    authority, roles = evidence_contract(tool_name)
    return EvidenceItem(
        evidence_id=call_id,
        call_id=call_id,
        source=tool_name,
        authority=authority,
        result_status=(
            EvidenceResultStatus.SUCCESS
            if success
            else EvidenceResultStatus.FAILURE
        ),
        targets=evidence_targets(args, ticket_id=ticket_id),
        observed_at=observed_at,
        supports_roles=roles,
        payload=output if success else {"error": error},
        redacted=False,
    )


def _context_targets(context: CaseContext) -> tuple[TargetReference, ...]:
    targets = [
        TargetReference(kind="ticket_id", value=str(context.ticket.ticket_id)),
    ]
    if context.identity.status is ResolutionStatus.RESOLVED:
        targets.append(
            TargetReference(kind="tenant_id", value=context.identity.selected_tenant_id),
        )
    return tuple(targets)


def build_case_context_evidence(context: CaseContext) -> tuple[EvidenceItem, ...]:
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
    ticket_target = (
        TargetReference(kind="ticket_id", value=str(context.ticket.ticket_id)),
    )
    for observation in context.visual_observations:
        items[observation.evidence_id] = EvidenceItem(
            evidence_id=observation.evidence_id,
            call_id=observation.evidence_id,
            source="case_context.visual",
            authority=EvidenceAuthority.CURRENT_SUPPORTING,
            result_status=EvidenceResultStatus.SUCCESS,
            targets=ticket_target,
            observed_at=context.built_at,
            supports_roles=(ClaimRole.PRODUCT_FACT, ClaimRole.LIMITATION),
            payload={
                "visible_text": observation.visible_text,
                "ui_state": observation.ui_state,
                "uncertainty": observation.uncertainty,
                "untrusted": True,
            },
        )
    return tuple(items.values())
