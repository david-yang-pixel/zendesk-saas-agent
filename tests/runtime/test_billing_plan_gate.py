from zd_agent.runtime.billing_plan_gate import billing_plan_claim_block_reason


def test_current_plan_claim_is_blocked_when_billing_disagrees() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Basic plan.",
        tool_call_log=[
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
    )

    assert blocker is not None
    assert "asserts basic" in blocker
    assert "returned team" in blocker


def test_current_plan_claim_accepts_matching_billing_sql() -> None:
    blocker = billing_plan_claim_block_reason(
        body="Your workspace is currently on the Team plan.",
        tool_call_log=[
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
    )

    assert blocker is None


def test_generic_plan_guidance_does_not_require_workspace_billing_lookup() -> None:
    blocker = billing_plan_claim_block_reason(
        body="The Team plan includes additional collaboration features.",
        tool_call_log=[],
    )

    assert blocker is None
