from zd_agent.runtime.billing_plan_gate import (
    billing_plan_claim_block_reason,
    resolved_target_tenant_id,
)


TENANT_1 = "11111111-1111-4111-8111-111111111111"
TENANT_2 = "22222222-2222-4222-8222-222222222222"


def _workspace_lookup(
    *,
    email: str = "form@example.com",
    tenant_ids: tuple[str, ...] = ("tenant-1",),
) -> dict:
    resolution = "unique" if len(tenant_ids) == 1 else "ambiguous"
    return {
        "tool": "inspector_exec",
        "args": {"email": email, "section": "tenants"},
        "success": True,
        "result_preview": (
            f"EMAIL_WORKSPACE_RESOLUTION={resolution}\n"
            f"EMAIL_WORKSPACE_COUNT={len(tenant_ids)}\n"
            f"EMAIL_WORKSPACE_TENANT_IDS={','.join(tenant_ids)}\n"
            "EMAIL_WORKSPACE_SOURCE=prod_membership"
        ),
    }


def test_current_plan_claim_is_blocked_when_billing_disagrees() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Basic plan.",
        tool_call_log=[
            _workspace_lookup(),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": "tenant-1", "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="form@example.com",
    )

    assert blocker is not None
    assert "asserts basic" in blocker
    assert "returned team" in blocker


def test_current_plan_claim_accepts_matching_billing_sql() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
            _workspace_lookup(),
            {
                "tool": "sql_query",
                "args": {
                    "database": "billing",
                    "sql": (
                        "SELECT tenant_id, plan, interval "
                        "FROM tenant_subscription_settings "
                        "WHERE tenant_id = 'tenant-1'"
                    ),
                },
                "success": True,
                "result_preview": (
                    "Rows: 1 | elapsed: 12ms\n\n"
                    "tenant_id,plan,interval\n"
                    "tenant-1,team,month\n"
                ),
            },
        ],
        form_email="form@example.com",
    )

    assert blocker is None


def test_generic_plan_guidance_does_not_require_workspace_billing_lookup() -> None:
    blocker = billing_plan_claim_block_reason(
        body="The Team plan includes additional collaboration features.",
        tool_call_log=[],
        form_email="form@example.com",
    )

    assert blocker is None


def test_current_plan_claim_requires_workspace_lookup_from_form_email() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
            _workspace_lookup(email="requester@example.com"),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": "tenant-1", "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="form@example.com",
        requester_email="requester@example.com",
    )

    assert blocker is not None
    assert "form email" in blocker.lower()


def test_multiple_email_workspaces_require_form_workspace_id() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
            _workspace_lookup(tenant_ids=("tenant-1", "tenant-2")),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": "tenant-2", "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="form@example.com",
    )

    assert blocker is not None
    assert "multiple workspaces" in blocker.lower()


def test_form_workspace_id_disambiguates_email_memberships() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
            _workspace_lookup(tenant_ids=(TENANT_1, TENANT_2)),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": TENANT_2, "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="form@example.com",
        form_workspace_id=TENANT_2,
    )

    assert blocker is None


def test_non_uuid_workspace_label_does_not_override_unique_email_workspace() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace currently has an active Professional subscription.",
        tool_call_log=[
            _workspace_lookup(tenant_ids=(TENANT_1,)),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": TENANT_1, "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=professional\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="form@example.com",
        form_workspace_id="Isaac_LK",
    )

    assert blocker is None


def test_unique_email_workspace_is_target_when_form_value_is_only_a_label() -> None:
    target = resolved_target_tenant_id(
        tool_call_log=[_workspace_lookup(tenant_ids=(TENANT_1,))],
        form_email="form@example.com",
        form_workspace_id="Isaac_LK",
    )

    assert target == TENANT_1


def test_non_uuid_workspace_label_cannot_disambiguate_multiple_email_workspaces() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
            _workspace_lookup(tenant_ids=(TENANT_1, TENANT_2)),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": TENANT_2, "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="form@example.com",
        form_workspace_id="Isaac_LK",
    )

    assert blocker is not None
    assert "multiple workspaces" in blocker.lower()


def test_canonical_form_workspace_id_must_belong_to_email_memberships() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
            _workspace_lookup(tenant_ids=(TENANT_1,)),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": TENANT_2, "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="form@example.com",
        form_workspace_id=TENANT_2,
    )

    assert blocker is not None
    assert "not among" in blocker.lower()


def test_billing_tenant_must_match_email_workspace_resolution() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
            _workspace_lookup(tenant_ids=("tenant-1",)),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": "tenant-2", "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="form@example.com",
    )

    assert blocker is not None
    assert "resolved workspace" in blocker.lower()


def test_email_shaped_workspace_id_selects_the_explicit_target_workspace() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
            _workspace_lookup(
                email="contact@example.com",
                tenant_ids=("contact-tenant",),
            ),
            _workspace_lookup(
                email="workspace-owner@example.com",
                tenant_ids=("target-tenant",),
            ),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": "target-tenant", "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="contact@example.com",
        form_workspace_id="workspace-owner@example.com",
    )

    assert blocker is None


def test_subscription_state_claim_cannot_use_billing_from_contact_email_tenant() -> None:
    blocker = billing_plan_claim_block_reason(
        body=(
            "I could not find an active paid-subscription record for that "
            "workspace, so a billing review is required."
        ),
        tool_call_log=[
            _workspace_lookup(
                email="contact@example.com",
                tenant_ids=("contact-tenant",),
            ),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": "contact-tenant", "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=basic\n"
                    "AUTHORITATIVE_BILLING_STATUS=none\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="contact@example.com",
        form_workspace_id="workspace-owner@example.com",
    )

    assert blocker is not None
    assert "workspace id" in blocker.lower()
    assert "section=tenants" in blocker.lower()


def test_subscription_state_claim_accepts_billing_for_email_shaped_target() -> None:
    blocker = billing_plan_claim_block_reason(
        body=(
            "The target workspace subscription is canceled and no longer active."
        ),
        tool_call_log=[
            _workspace_lookup(
                email="contact@example.com",
                tenant_ids=("contact-tenant",),
            ),
            _workspace_lookup(
                email="workspace-owner@example.com",
                tenant_ids=("target-tenant",),
            ),
            {
                "tool": "inspector_exec",
                "args": {"tenant_id": "target-tenant", "section": "billing"},
                "success": True,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=basic\n"
                    "AUTHORITATIVE_BILLING_STATUS=canceled\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ],
        form_email="contact@example.com",
        form_workspace_id="workspace-owner@example.com",
    )

    assert blocker is None
