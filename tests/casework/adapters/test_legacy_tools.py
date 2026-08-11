from datetime import datetime, timezone

from zd_agent.casework.adapters.legacy_tools import (
    InspectorBillingReader,
    InspectorIdentityReader,
    LegacySessionToolCaller,
    LegacyToolObservation,
)
from zd_agent.casework.domain.enums import AdapterErrorCode, LookupStatus
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


class FakeCaller:
    def __init__(self, observation: LegacyToolObservation) -> None:
        self.observation = observation
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call(
        self,
        tool_name: str,
        args: dict[str, object],
    ) -> LegacyToolObservation:
        self.calls.append((tool_name, args))
        return self.observation


class WriteTool(Tool):
    name = "write_tool"
    description = "must never run in shadow mode"
    parameters: dict[str, object] = {"type": "object"}
    is_write_operation = True

    def __init__(self) -> None:
        self.executed = False

    async def execute(self, args: dict, session: AgentSession) -> ToolResult:
        self.executed = True
        return ToolResult(output="written")


async def test_identity_reader_uses_structured_email_memberships() -> None:
    caller = FakeCaller(
        LegacyToolObservation(
            call_id="call-identity",
            success=True,
            output="EMAIL_WORKSPACE_RESOLUTION=unique",
            metadata={
                "workspace_resolution": {
                    "source": "prod_membership",
                    "status": "unique",
                    "count": 1,
                    "tenant_ids": [TENANT],
                    "candidates": [{"tenant_id": TENANT, "name": "Example Workspace"}],
                },
            },
        ),
    )
    reader = InspectorIdentityReader(caller)

    result = await reader.find_workspaces("Owner@Example.test")

    assert caller.calls == [
        ("inspector_exec", {"email": "owner@example.test", "section": "tenants"}),
    ]
    assert result.status is LookupStatus.SUCCESS
    assert result.evidence_id == "call-identity"
    assert result.candidates[0].tenant_id == TENANT
    assert result.candidates[0].workspace_name == "Example Workspace"


async def test_identity_reader_fails_closed_on_malformed_metadata() -> None:
    caller = FakeCaller(
        LegacyToolObservation(
            call_id="call-malformed",
            success=True,
            output="unexpected output",
            metadata={},
        ),
    )

    result = await InspectorIdentityReader(caller).find_workspaces("owner@example.test")

    assert result.status is LookupStatus.FAILED
    assert result.error_code is AdapterErrorCode.MALFORMED_RESPONSE


async def test_identity_reader_maps_transient_tool_failure() -> None:
    caller = FakeCaller(
        LegacyToolObservation(
            call_id="call-timeout",
            success=False,
            error="inspector execution timed out after 45 seconds",
        ),
    )

    result = await InspectorIdentityReader(caller).find_workspaces("owner@example.test")

    assert result.status is LookupStatus.FAILED
    assert result.error_code is AdapterErrorCode.TIMEOUT


async def test_billing_reader_accepts_only_authoritative_tenant_scoped_result() -> None:
    caller = FakeCaller(
        LegacyToolObservation(
            call_id="call-billing",
            success=True,
            output=(
                "AUTHORITATIVE_BILLING_PLAN=professional\n"
                "AUTHORITATIVE_BILLING_STATUS=active\n"
                "AUTHORITATIVE_BILLING_SOURCE=billing_db\n"
                "| Billing Interval | year |\n"
                "| Stripe Status | active |\n"
                "| Entitlement Status | active |\n"
                "| Cancellation Status | scheduled |\n"
                "| Renewal Status | disabled |"
            ),
        ),
    )
    reader = InspectorBillingReader(caller, now=lambda: NOW)

    result = await reader.read_billing(TENANT)

    assert caller.calls == [
        ("inspector_exec", {"tenant_id": TENANT, "section": "billing"}),
    ]
    assert result.status is LookupStatus.SUCCESS
    assert result.tenant_id == TENANT
    assert result.plan == "professional"
    assert result.subscription_status == "active"
    assert result.interval == "year"
    assert result.cancellation_status == "scheduled"
    assert result.renewal_status == "disabled"


async def test_billing_reader_rejects_non_authoritative_plan_metadata() -> None:
    caller = FakeCaller(
        LegacyToolObservation(
            call_id="call-unverified",
            success=True,
            output=(
                "AUTHORITATIVE_BILLING_PLAN=unknown\n"
                "AUTHORITATIVE_BILLING_STATUS=unknown\n"
                "AUTHORITATIVE_BILLING_SOURCE=unverified\n"
                "| Plan | professional |"
            ),
        ),
    )

    result = await InspectorBillingReader(caller, now=lambda: NOW).read_billing(TENANT)

    assert result.status is LookupStatus.FAILED
    assert result.error_code is AdapterErrorCode.MALFORMED_RESPONSE
    assert result.plan == ""


async def test_legacy_tool_caller_blocks_every_write_tool_in_shadow_mode() -> None:
    registry = ToolRegistry()
    tool = WriteTool()
    registry.register(tool)
    caller = LegacySessionToolCaller(registry, AgentSession(ticket_id=123))

    observation = await caller.call("write_tool", {})

    assert observation.success is False
    assert "blocked write tool" in observation.error
    assert tool.executed is False
