from __future__ import annotations

import json

from zd_agent.casework.domain.enums import OwnershipAction
from zd_agent.casework.domain.models import CaseworkRun, HumanActionRequest


def render_ownership_packet(action: HumanActionRequest | None) -> str:
    if action is None:
        return ""
    lines = [
        f"Target: {action.ownership_action.value}",
        f"Reason: {action.reason_code.value}",
        f"Summary: {action.summary}",
        f"Customer impact: {action.customer_impact}",
        "Completed checks:",
        *(f"- {value}" for value in action.completed_checks),
        f"Unresolved action: {action.unresolved_action}",
        "Acceptance criteria:",
        *(f"- {value}" for value in action.acceptance_criteria),
        "Evidence references: " + ", ".join(action.evidence_ids),
    ]
    return "\n".join(lines)


def render_casework_audit(run: CaseworkRun) -> str:
    assessment = run.assessment_build.assessment
    decision = run.decision
    payload = {
        "execution_mode": run.execution_mode.value,
        "ticket_generation": run.context.ticket.generation,
        "source_event_id": run.context.ticket.latest_event_id,
        "identity": {
            "status": run.context.identity.status.value,
            "method": run.context.identity.method.value,
            "selected_tenant_id": run.context.identity.selected_tenant_id,
        },
        "decision": {
            "disposition": decision.disposition.value,
            "reply_kind": decision.reply_kind.value,
            "ownership_action": decision.ownership_action.value,
            "reason_code": decision.reason_code.value,
            "evidence_ids": decision.evidence_ids,
            "required_customer_fields": decision.required_customer_fields,
        },
        "claims": [
            {
                "claim_id": claim.claim_id,
                "role": claim.role.value,
                "status": claim.status.value,
                "statement": claim.statement,
                "supporting_evidence_ids": claim.supporting_evidence_ids,
            }
            for claim in assessment.admitted_claims
        ],
        "claim_admissions": [
            admission.model_dump(mode="json")
            for admission in run.assessment_build.admissions
        ],
        "ownership_packet": (
            render_ownership_packet(assessment.human_action)
            if decision.ownership_action is not OwnershipAction.NONE
            else ""
        ),
    }
    return "Zendesk Agent v2 casework audit\n\n" + json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )
