from datetime import datetime, timezone

from zd_agent.casework.adapters.legacy_evidence import build_legacy_case_material
from zd_agent.casework.domain.enums import (
    BillingBindingStatus,
    ClaimRole,
    EvidenceAuthority,
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
from zd_agent.session.agent_session import AgentSession


NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


def _context() -> CaseContext:
    identity = IdentityResolution(
        contact_email="owner@example.test",
        target_identity="owner@example.test",
        status=ResolutionStatus.RESOLVED,
        method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
        candidates=(WorkspaceCandidate(tenant_id=TENANT, workspace_name="Workspace"),),
        selected_tenant_id=TENANT,
        selected_workspace_name="Workspace",
        evidence_ids=("ev-form", "ev-identity"),
    )
    billing = BillingSnapshot(
        tenant_id=TENANT,
        identity_tenant_id=TENANT,
        plan="professional",
        subscription_status="active",
        observed_at=NOW,
        evidence_ids=("ev-form", "ev-identity", "ev-billing"),
    )
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=2,
            status="open",
            latest_event_id="event-2",
            latest_event_at=NOW,
            questions=("Why did this fail?",),
            form_identity=FormIdentity(email="owner@example.test"),
        ),
        built_at=NOW,
        identity=identity,
        billing_required=True,
        billing=BillingBinding(
            status=BillingBindingStatus.RESOLVED,
            snapshot=billing,
            evidence_ids=billing.evidence_ids,
        ),
    )


def test_material_uses_full_tool_message_not_truncated_session_preview() -> None:
    session = AgentSession(ticket_id=4001)
    session.tool_call_log = [
        {
            "call_id": "call-runtime",
            "tool": "signoz_search_logs",
            "args": {"tenant_id": TENANT, "app_id": "app-1"},
            "success": True,
            "result_preview": "TRUNCATED",
        },
    ]
    session.messages = [
        {
            "role": "tool",
            "tool_call_id": "call-runtime",
            "name": "signoz_search_logs",
            "content": "EVIDENCE_CALL_ID=call-runtime\n\nFULL_CURRENT_RUNTIME_EVIDENCE",
        },
    ]

    material = build_legacy_case_material(
        session=session,
        context=_context(),
        read_tool_names=frozenset({"signoz_search_logs"}),
        observed_at=NOW,
    )

    item = next(value for value in material.evidence if value.evidence_id == "call-runtime")
    assert item.payload == "FULL_CURRENT_RUNTIME_EVIDENCE"
    assert "TRUNCATED" not in str(item.payload)
    assert item.authority is EvidenceAuthority.CURRENT_AUTHORITATIVE
    assert ClaimRole.FAILING_BOUNDARY in item.supports_roles
    assert item.target_value("tenant_id") == TENANT
    assert item.target_value("app_id") == "app-1"


def test_source_evidence_is_supporting_and_can_supply_causal_mechanism() -> None:
    session = AgentSession(ticket_id=4001)
    session.tool_call_log = [
        {
            "call_id": "call-source",
            "tool": "source_search",
            "args": {"query": "collection binding"},
            "success": True,
            "result_preview": "source preview",
        },
    ]
    session.messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-source",
                    "content": "aligned source implementation",
                },
            ],
        },
    ]

    material = build_legacy_case_material(
        session=session,
        context=_context(),
        read_tool_names=frozenset({"source_search"}),
        observed_at=NOW,
    )

    item = next(value for value in material.evidence if value.evidence_id == "call-source")
    assert item.authority is EvidenceAuthority.CURRENT_SUPPORTING
    assert ClaimRole.ROOT_CAUSE in item.supports_roles


def test_failed_or_non_read_calls_never_become_evidence() -> None:
    session = AgentSession(ticket_id=4001)
    session.tool_call_log = [
        {
            "call_id": "call-failed",
            "tool": "sql_query",
            "args": {},
            "success": False,
            "result_preview": "failed",
        },
        {
            "call_id": "call-write",
            "tool": "zendesk_public_reply",
            "args": {"body": "Public body", "reply_type": "handoff"},
            "success": True,
            "result_preview": "sent",
        },
    ]
    session.messages = [
        {"role": "tool", "tool_call_id": "call-failed", "content": "failed"},
        {"role": "tool", "tool_call_id": "call-write", "content": "sent"},
    ]
    session.analysis = "Legacy internal analysis"

    material = build_legacy_case_material(
        session=session,
        context=_context(),
        read_tool_names=frozenset({"sql_query"}),
        observed_at=NOW,
    )

    ids = {item.evidence_id for item in material.evidence}
    assert "call-failed" not in ids
    assert "call-write" not in ids
    assert {"ev-form", "ev-identity", "ev-billing"}.issubset(ids)
    assert material.legacy_reply_type == "handoff"
    assert material.legacy_reply_body == "Public body"
    assert material.legacy_analysis == "Legacy internal analysis"
