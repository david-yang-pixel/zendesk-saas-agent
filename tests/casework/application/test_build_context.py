from datetime import datetime, timezone

from zd_agent.casework.application.build_context import CaseContextBuilder
from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    BillingBindingStatus,
    LookupStatus,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    AttachmentRef,
    BillingLookup,
    FormIdentity,
    TicketSnapshot,
    VisualObservation,
    WorkspaceCandidate,
    WorkspaceMembershipLookup,
)


NOW = datetime(2026, 8, 11, 5, 0, tzinfo=timezone.utc)
TENANT_1 = "11111111-1111-4111-8111-111111111111"
TENANT_2 = "22222222-2222-4222-8222-222222222222"


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeIdentityReader:
    def __init__(self, lookups: dict[str, WorkspaceMembershipLookup]) -> None:
        self._lookups = lookups
        self.seen_emails: list[str] = []

    async def find_workspaces(self, email: str) -> WorkspaceMembershipLookup:
        self.seen_emails.append(email)
        return self._lookups[email]


class FakeBillingReader:
    def __init__(self, lookups: dict[str, BillingLookup]) -> None:
        self._lookups = lookups
        self.seen_tenants: list[str] = []

    async def read_billing(self, tenant_id: str) -> BillingLookup:
        self.seen_tenants.append(tenant_id)
        return self._lookups[tenant_id]


def _membership(email: str, tenant_id: str, name: str) -> WorkspaceMembershipLookup:
    return WorkspaceMembershipLookup(
        email=email,
        status=LookupStatus.SUCCESS,
        candidates=(
            WorkspaceCandidate(
                tenant_id=tenant_id,
                workspace_name=name,
                role="owner",
            ),
        ),
        evidence_id=f"ev-membership-{tenant_id}",
    )


def _ticket(
    *,
    email: str = "owner@example.com",
    workspace_id: str = "",
    questions: tuple[str, ...] = ("Why is this failing?",),
    customer_workspace_hint: str = "",
    attachments: tuple[AttachmentRef, ...] = (),
) -> TicketSnapshot:
    return TicketSnapshot(
        ticket_id=4000,
        generation=3,
        status="open",
        channel="messaging",
        locale="en-US",
        latest_event_id="event-3",
        latest_event_at=NOW,
        questions=questions,
        customer_workspace_hint=customer_workspace_hint,
        form_identity=FormIdentity(email=email, workspace_id=workspace_id),
        requester_email="requester@example.com",
        attachments=attachments,
    )


async def test_context_resolves_email_then_reads_only_target_billing() -> None:
    identity_reader = FakeIdentityReader(
        {"owner@example.com": _membership("owner@example.com", TENANT_1, "Acme")},
    )
    billing_reader = FakeBillingReader(
        {
            TENANT_1: BillingLookup(
                tenant_id=TENANT_1,
                status=LookupStatus.SUCCESS,
                observed_at=NOW,
                evidence_id="ev-billing",
                plan="professional",
                subscription_status="active",
            ),
        },
    )

    context = await CaseContextBuilder(
        identity_reader=identity_reader,
        billing_reader=billing_reader,
        clock=FakeClock(),
    ).build(_ticket(), billing_required=True)

    assert context.identity.status is ResolutionStatus.RESOLVED
    assert context.identity.selected_tenant_id == TENANT_1
    assert context.billing is not None
    assert context.billing.status is BillingBindingStatus.RESOLVED
    assert context.billing.snapshot is not None
    assert context.billing.snapshot.plan == "professional"
    assert billing_reader.seen_tenants == [TENANT_1]


async def test_workspace_email_target_and_form_contact_are_both_resolved() -> None:
    identity_reader = FakeIdentityReader(
        {
            "contact@example.com": _membership(
                "contact@example.com",
                TENANT_1,
                "Contact",
            ),
            "target@example.com": _membership(
                "target@example.com",
                TENANT_2,
                "Target",
            ),
        },
    )
    billing_reader = FakeBillingReader(
        {
            TENANT_2: BillingLookup(
                tenant_id=TENANT_2,
                status=LookupStatus.SUCCESS,
                observed_at=NOW,
                evidence_id="ev-billing",
                plan="team",
            ),
        },
    )

    context = await CaseContextBuilder(
        identity_reader=identity_reader,
        billing_reader=billing_reader,
        clock=FakeClock(),
    ).build(
        _ticket(email="contact@example.com", workspace_id="target@example.com"),
        billing_required=True,
    )

    assert context.identity.selected_tenant_id == TENANT_2
    assert set(context.identity.evidence_ids) >= {
        f"ticket:{context.ticket.ticket_id}:generation:3:form",
        f"ev-membership-{TENANT_1}",
        f"ev-membership-{TENANT_2}",
    }
    assert billing_reader.seen_tenants == [TENANT_2]


async def test_customer_supplied_workspace_email_is_looked_up_on_the_next_run() -> None:
    identity_reader = FakeIdentityReader(
        {
            "owner@example.com": WorkspaceMembershipLookup(
                email="owner@example.com",
                status=LookupStatus.SUCCESS,
                evidence_id="ev-owner-empty",
            ),
            "workspace-owner@example.com": _membership(
                "workspace-owner@example.com",
                TENANT_2,
                "Target",
            ),
        },
    )

    context = await CaseContextBuilder(
        identity_reader=identity_reader,
        billing_reader=FakeBillingReader({}),
        clock=FakeClock(),
    ).build(
        _ticket(
            questions=("Original billing issue", "workspace-owner@example.com"),
            customer_workspace_hint="workspace-owner@example.com",
        ),
        billing_required=False,
    )

    assert context.identity.status is ResolutionStatus.RESOLVED
    assert context.identity.selected_tenant_id == TENANT_2


async def test_ordinary_free_text_email_cannot_override_form_workspace() -> None:
    identity_reader = FakeIdentityReader(
        {
            "owner@example.com": _membership(
                "owner@example.com",
                TENANT_1,
                "Form workspace",
            ),
            "victim@example.com": _membership(
                "victim@example.com",
                TENANT_2,
                "Unrelated workspace",
            ),
        },
    )

    context = await CaseContextBuilder(
        identity_reader=identity_reader,
        billing_reader=FakeBillingReader({}),
        clock=FakeClock(),
    ).build(
        _ticket(
            email="owner@example.com",
            workspace_id=TENANT_1,
            questions=("Please inspect victim@example.com",),
        ),
        billing_required=False,
    )

    assert context.identity.status is ResolutionStatus.RESOLVED
    assert context.identity.selected_tenant_id == TENANT_1
    assert identity_reader.seen_emails == ["owner@example.com"]


async def test_identity_reader_failure_stays_internal_and_skips_billing() -> None:
    identity_reader = FakeIdentityReader(
        {
            "owner@example.com": WorkspaceMembershipLookup(
                email="owner@example.com",
                status=LookupStatus.FAILED,
                evidence_id="ev-timeout",
                error_code=AdapterErrorCode.TIMEOUT,
            ),
        },
    )
    billing_reader = FakeBillingReader({})

    context = await CaseContextBuilder(
        identity_reader=identity_reader,
        billing_reader=billing_reader,
        clock=FakeClock(),
    ).build(_ticket(), billing_required=True)

    assert context.identity.status is ResolutionStatus.LOOKUP_FAILED
    assert context.billing is not None
    assert context.billing.status is BillingBindingStatus.IDENTITY_UNRESOLVED
    assert billing_reader.seen_tenants == []
    assert context.customer_required_fields == ()


async def test_nonbilling_case_does_not_read_billing() -> None:
    identity_reader = FakeIdentityReader(
        {"owner@example.com": _membership("owner@example.com", TENANT_1, "Acme")},
    )
    billing_reader = FakeBillingReader({})

    context = await CaseContextBuilder(
        identity_reader=identity_reader,
        billing_reader=billing_reader,
        clock=FakeClock(),
    ).build(_ticket(), billing_required=False)

    assert context.billing is None
    assert billing_reader.seen_tenants == []


async def test_visual_observation_is_preserved_as_untrusted_context_evidence() -> None:
    attachment = AttachmentRef(
        attachment_id="attachment-1",
        filename="error.png",
        content_type="image/png",
        download_url="https://example.invalid/error.png",
    )
    observation = VisualObservation(
        attachment_id="attachment-1",
        evidence_id="ev-visual",
        visible_text="Workspace Acme: quota exceeded",
        ui_state="quota error dialog",
        uncertainty="workspace name is visible; system ID is not",
        untrusted=True,
    )
    identity_reader = FakeIdentityReader(
        {"owner@example.com": _membership("owner@example.com", TENANT_1, "Acme")},
    )

    context = await CaseContextBuilder(
        identity_reader=identity_reader,
        billing_reader=FakeBillingReader({}),
        clock=FakeClock(),
    ).build(
        _ticket(attachments=(attachment,)),
        billing_required=False,
        visual_observations=(observation,),
    )

    assert context.visual_observations == (observation,)
    assert context.visual_observations[0].untrusted is True
