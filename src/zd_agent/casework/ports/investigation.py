from __future__ import annotations

from typing import Protocol

from zd_agent.casework.domain.models import CaseContext, InvestigationResult


class CaseInvestigator(Protocol):
    async def investigate(self, context: CaseContext) -> InvestigationResult: ...
