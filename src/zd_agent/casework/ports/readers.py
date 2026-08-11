from __future__ import annotations

from datetime import datetime
from typing import Protocol

from zd_agent.casework.domain.models import BillingLookup, WorkspaceMembershipLookup


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdentityReader(Protocol):
    async def find_workspaces(self, email: str) -> WorkspaceMembershipLookup: ...


class BillingReader(Protocol):
    async def read_billing(self, tenant_id: str) -> BillingLookup: ...

