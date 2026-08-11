from __future__ import annotations

import re

from zd_agent.casework.domain.models import TicketSnapshot


_BILLING_RE = re.compile(
    r"\b(?:billing|charge|credits?|document limit|invoice|payment|pricing|quota|"
    r"refund|renewal|subscription)\b|"
    r"\b(?:what|which|current|active|paid|free|basic|team|professional|enterprise)"
    r"\b.{0,20}\bplan\b|"
    r"\bplan\b.{0,20}\b(?:active|using|subscription|upgrade|downgrade|billing)\b|"
    r"套餐|扣费|支付|账单|续费|退款|配额",
    re.IGNORECASE,
)


def billing_context_required(ticket: TicketSnapshot, *, ticket_type: str = "") -> bool:
    if ticket_type.casefold() in {"billing", "refund"}:
        return True
    return bool(_BILLING_RE.search("\n".join(ticket.questions)))
