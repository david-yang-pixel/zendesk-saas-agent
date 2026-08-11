from __future__ import annotations

from typing import Protocol

from zd_agent.casework.domain.models import CaseworkEffectReceipt, CaseworkRun


class CaseworkEffectPort(Protocol):
    async def apply(
        self,
        *,
        run: CaseworkRun,
        delivery_attempt_id: str,
        dry_run: bool,
    ) -> CaseworkEffectReceipt: ...
