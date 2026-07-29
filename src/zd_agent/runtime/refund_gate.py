"""Runtime guardrails for refund public replies."""

from __future__ import annotations

import re
from typing import Any

_REFUND_ELIGIBILITY_RE = re.compile(
    r"("
    r"\bqualif(?:y|ies|ied|ying)\b|"
    r"\beligible for (?:a )?refund\b|"
    r"\bnot eligible\b|"
    r"\bdoes not qualify\b|"
    r"\bfull refund\b|"
    r"\bpartial refund\b|"
    r"\bpro-?rated refund\b|"
    r"\brefund amount\b|"
    r"\brefund of approximately\b|"
    r"\brefund (?:has been|is) approved\b|"
    r"\bwe (?:will|can) (?:issue|process) (?:the |your )?refund\b|"
    r"\bwithin \d+(?:\s*-\s*\d+)? business days\b|"
    r"符合.{0,12}退款|"
    r"不符合.{0,12}退款|"
    r"退款申请.{0,12}符合|"
    r"退款条件|"
    r"预计金额|"
    r"全额退款|"
    r"部分退款|"
    r"按比例退款"
    r")",
    re.IGNORECASE,
)

_OWNER_ROLE_RE = re.compile(
    r"(^|[\n,])\s*owner\s*([,\n\r]|$)|"
    r"\brole\b\s*[=:]\s*['\"]?owner\b|"
    r"['\"]role['\"]\s*:\s*['\"]owner['\"]",
    re.IGNORECASE,
)


def refund_owner_gate_block_reason(
    *,
    body: str,
    session: Any,
) -> str | None:
    """Block automated refund decisions while preserving factual replies.

    The agent may tell a verified owner factual subscription/payment state, but
    eligibility, amount, approval, processing, and timing remain human business
    decisions. Ownership is still checked first so the returned blocker tells
    the agent the correct next step.
    """
    if (getattr(session, "ticket_type", "") or "").lower() != "refund":
        return None
    if not _looks_like_refund_eligibility_judgment(body):
        return None
    if _has_owner_gate_evidence(getattr(session, "tool_call_log", []) or []):
        return (
            "Refund decision gate blocked this public reply: the requester is "
            "verified as the workspace owner, but refund eligibility, amount, "
            "approval, processing, or timing still requires human business "
            "approval. Reply only with verified technical/payment facts and "
            "use reply_type=handoff for the decision."
        )
    return (
        "Refund owner gate blocked this public reply: refund eligibility or "
        "amount cannot be judged until sql_query verifies that the requester "
        "is the workspace owner via tenant_account_joins + accounts."
    )


def _looks_like_refund_eligibility_judgment(body: str) -> bool:
    return bool(_REFUND_ELIGIBILITY_RE.search(body or ""))


def _has_owner_gate_evidence(tool_call_log: list[dict[str, Any]]) -> bool:
    for item in tool_call_log or []:
        if not isinstance(item, dict):
            continue
        if item.get("tool") != "sql_query" or item.get("success") is not True:
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        sql = str(args.get("sql") or "").lower()
        database = str(args.get("database") or "").lower()
        if database != "prod":
            continue
        if "tenant_account_joins" not in sql or "accounts" not in sql:
            continue
        evidence = _tool_item_text(item)
        if _OWNER_ROLE_RE.search(evidence):
            return True
    return False


def _tool_item_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("result_preview", "output"):
        if item.get(key):
            parts.append(str(item[key]))
    return "\n".join(parts)
