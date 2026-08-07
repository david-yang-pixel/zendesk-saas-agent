from zd_agent.runtime.billing_plan_gate import (
    billing_plan_claim_block_reason,
    resolved_target_tenant_id,
    workspace_identity_request_block_reason,
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


def test_workspace_identity_ask_is_blocked_when_form_email_is_unique() -> None:
    blocker = workspace_identity_request_block_reason(
        body="Please send the full Workspace ID from your settings.",
        tool_call_log=[_workspace_lookup(tenant_ids=(TENANT_1,))],
        form_email="form@example.com",
        form_workspace_id="Isaac_LK",
    )

    assert blocker is not None
    assert "support-side" in blocker.lower()
    assert "already resolved" in blocker.lower()


def test_workspace_name_confirmation_is_blocked_when_email_is_unique() -> None:
    blocker = workspace_identity_request_block_reason(
        body=(
            "Before I route this request, please confirm that the affected "
            "workspace is named Isaac_LK's Workspace."
        ),
        tool_call_log=[_workspace_lookup(tenant_ids=(TENANT_1,))],
        form_email="form@example.com",
        form_workspace_id="Isaac_LK",
    )

    assert blocker is not None
    assert "already resolved" in blocker.lower()


def test_workspace_identity_ask_requires_support_lookup_before_customer_ask() -> None:
    blocker = workspace_identity_request_block_reason(
        body="Could you confirm the affected workspace or share its Workspace ID?",
        tool_call_log=[],
        form_email="form@example.com",
    )

    assert blocker is not None
    assert "support_action_required" in blocker.lower()
    assert "section=tenants" in blocker.lower()
    assert "do not ask the customer" in blocker.lower()


def test_workspace_identity_ask_requires_retry_after_unverified_lookup() -> None:
    lookup = _workspace_lookup(tenant_ids=(TENANT_1,))
    lookup["result_preview"] = (
        "EMAIL_WORKSPACE_RESOLUTION=unique\n"
        "EMAIL_WORKSPACE_COUNT=1\n"
        f"EMAIL_WORKSPACE_TENANT_IDS={TENANT_1}\n"
        "EMAIL_WORKSPACE_SOURCE=unknown"
    )

    blocker = workspace_identity_request_block_reason(
        body="Please provide the system-generated Workspace ID.",
        tool_call_log=[lookup],
        form_email="form@example.com",
    )

    assert blocker is not None
    assert "support_action_required" in blocker.lower()
    assert "repeat" in blocker.lower()


def test_workspace_identity_ask_is_allowed_for_verified_ambiguity() -> None:
    blocker = workspace_identity_request_block_reason(
        body="Please provide the Workspace ID for the affected workspace.",
        tool_call_log=[_workspace_lookup(tenant_ids=(TENANT_1, TENANT_2))],
        form_email="form@example.com",
        form_workspace_id="display-label",
    )

    assert blocker is None


def test_workspace_identity_ask_is_blocked_when_form_uuid_disambiguates() -> None:
    blocker = workspace_identity_request_block_reason(
        body="Please provide the Workspace ID for the affected workspace.",
        tool_call_log=[_workspace_lookup(tenant_ids=(TENANT_1, TENANT_2))],
        form_email="form@example.com",
        form_workspace_id=TENANT_2,
    )

    assert blocker is not None
    assert "already resolved" in blocker.lower()


def test_workspace_identity_ask_is_allowed_after_verified_no_membership() -> None:
    lookup = {
        "tool": "inspector_exec",
        "args": {"email": "form@example.com", "section": "tenants"},
        "success": True,
        "result_preview": (
            "EMAIL_WORKSPACE_RESOLUTION=none\n"
            "EMAIL_WORKSPACE_COUNT=0\n"
            "EMAIL_WORKSPACE_TENANT_IDS=\n"
            "EMAIL_WORKSPACE_SOURCE=prod_membership"
        ),
    }

    blocker = workspace_identity_request_block_reason(
        body="Please provide the Workspace ID for the affected workspace.",
        tool_call_log=[lookup],
        form_email="form@example.com",
    )

    assert blocker is None


def test_workspace_identity_ask_is_blocked_when_form_uuid_is_already_known(
) -> None:
    lookup = {
        "tool": "inspector_exec",
        "args": {"email": "form@example.com", "section": "tenants"},
        "success": True,
        "result_preview": (
            "EMAIL_WORKSPACE_RESOLUTION=none\n"
            "EMAIL_WORKSPACE_COUNT=0\n"
            "EMAIL_WORKSPACE_TENANT_IDS=\n"
            "EMAIL_WORKSPACE_SOURCE=prod_membership"
        ),
    }

    blocker = workspace_identity_request_block_reason(
        body="Please provide the Workspace ID for the affected workspace.",
        tool_call_log=[lookup],
        form_email="form@example.com",
        form_workspace_id=TENANT_1,
    )

    assert blocker is not None
    assert "already contains" in blocker.lower()
    assert TENANT_1 in blocker


def test_unrelated_customer_only_question_is_not_identity_blocked() -> None:
    blocker = workspace_identity_request_block_reason(
        body="Please share the exact UTC time of the failed request.",
        tool_call_log=[_workspace_lookup(tenant_ids=(TENANT_1,))],
        form_email="form@example.com",
    )

    assert blocker is None
