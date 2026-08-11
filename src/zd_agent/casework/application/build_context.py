from __future__ import annotations

import asyncio

from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    LookupStatus,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    BillingBinding,
    BillingLookup,
    CaseContext,
    IdentityResolution,
    TicketSnapshot,
    VisualObservation,
    WorkspaceMembershipLookup,
)
from zd_agent.casework.policies.billing import bind_billing_snapshot
from zd_agent.casework.policies.identity import (
    identity_lookup_emails,
    resolve_workspace_identity,
)
from zd_agent.casework.ports.readers import BillingReader, Clock, IdentityReader


class CaseContextBuilder:
    def __init__(
        self,
        *,
        identity_reader: IdentityReader,
        billing_reader: BillingReader,
        clock: Clock,
    ) -> None:
        self._identity_reader = identity_reader
        self._billing_reader = billing_reader
        self._clock = clock

    async def _read_identity(self, email: str) -> WorkspaceMembershipLookup:
        try:
            return await self._identity_reader.find_workspaces(email)
        except Exception:
            return WorkspaceMembershipLookup(
                email=email,
                status=LookupStatus.FAILED,
                evidence_id=f"identity-error:{email}",
                error_code=AdapterErrorCode.PERMANENT_FAILURE,
            )

    async def _read_billing(self, tenant_id: str) -> BillingLookup:
        try:
            return await self._billing_reader.read_billing(tenant_id)
        except Exception:
            return BillingLookup(
                tenant_id=tenant_id,
                status=LookupStatus.FAILED,
                observed_at=self._clock.now(),
                evidence_id=f"billing-error:{tenant_id}",
                error_code=AdapterErrorCode.PERMANENT_FAILURE,
            )

    async def _resolve_identity(self, ticket: TicketSnapshot) -> IdentityResolution:
        form = ticket.form_identity
        emails = identity_lookup_emails(
            form_email=form.email,
            form_workspace_id=form.workspace_id,
            requester_email=ticket.requester_email,
            customer_workspace_hint=ticket.customer_workspace_hint,
        )
        lookups = tuple(
            await asyncio.gather(*(self._read_identity(email) for email in emails)),
        )
        return resolve_workspace_identity(
            form_email=form.email,
            form_workspace_id=form.workspace_id,
            requester_email=ticket.requester_email,
            lookups=lookups,
            form_evidence_id=(
                f"ticket:{ticket.ticket_id}:generation:{ticket.generation}:form"
            ),
            # A customer who was asked to choose among verified email
            # memberships can reply with the exact workspace name.  The
            # identity policy compares the entire latest issue text only
            # against that bounded candidate set; it never performs fuzzy or
            # cross-account lookup from free text.
            customer_workspace_hint=ticket.customer_workspace_hint,
        )

    async def _bind_billing(
        self,
        identity: IdentityResolution,
    ) -> BillingBinding:
        lookup = None
        if identity.status is ResolutionStatus.RESOLVED:
            lookup = await self._read_billing(identity.selected_tenant_id)
        return bind_billing_snapshot(identity=identity, lookup=lookup)

    @staticmethod
    def _warnings(
        identity: IdentityResolution,
        billing: BillingBinding | None,
    ) -> tuple[str, ...]:
        return tuple(
            detail
            for detail in (identity.detail, billing.detail if billing else "")
            if detail
        )

    async def build(
        self,
        ticket: TicketSnapshot,
        *,
        billing_required: bool,
        automation_attempt: int = 1,
        visual_observations: tuple[VisualObservation, ...] = (),
    ) -> CaseContext:
        identity = await self._resolve_identity(ticket)
        billing = await self._bind_billing(identity) if billing_required else None
        return CaseContext(
            ticket=ticket,
            built_at=self._clock.now(),
            identity=identity,
            automation_attempt=automation_attempt,
            billing_required=billing_required,
            billing=billing,
            visual_observations=visual_observations,
            warnings=self._warnings(identity, billing),
        )
