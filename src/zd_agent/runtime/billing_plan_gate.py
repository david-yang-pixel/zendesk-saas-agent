"""Fail-closed evidence gate for customer-visible subscription-plan claims."""

from __future__ import annotations

import csv
import re
from typing import Any, Final

_PLAN_CODES: Final = {
    "basic",
    "sandbox",
    "professional",
    "team",
    "enterprise",
}
_PLAN_WITH_LABEL_RE: Final = re.compile(
    r"\b(basic|sandbox|professional|team|enterprise)\s+plan\b",
    re.IGNORECASE,
)
_CURRENT_PLAN_CONTEXT_RE: Final = re.compile(
    r"\b(?:your|workspace|account|subscription|associated|current(?:ly)?|recorded)\b",
    re.IGNORECASE,
)
_INSPECTOR_PLAN_RE: Final = re.compile(
    r"^AUTHORITATIVE_BILLING_PLAN=(basic|sandbox|professional|team|enterprise)$",
    re.IGNORECASE | re.MULTILINE,
)


def _asserted_current_plans(body: str) -> set[str]:
    plans: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", body or ""):
        if not _CURRENT_PLAN_CONTEXT_RE.search(sentence):
            continue
        plans.update(match.lower() for match in _PLAN_WITH_LABEL_RE.findall(sentence))
    return plans


def _plans_from_billing_csv(text: str) -> set[str]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        try:
            header = [cell.strip().lower() for cell in next(csv.reader([line]))]
        except (csv.Error, StopIteration):
            continue
        if "tenant_id" not in header or "plan" not in header:
            continue
        plan_index = header.index("plan")
        plans: set[str] = set()
        for row_line in lines[index + 1 :]:
            try:
                row = [cell.strip().lower() for cell in next(csv.reader([row_line]))]
            except (csv.Error, StopIteration):
                continue
            if len(row) <= plan_index:
                continue
            if row[plan_index] in _PLAN_CODES:
                plans.add(row[plan_index])
        return plans
    return set()


def _authoritative_plans(tool_call_log: list[dict[str, Any]]) -> set[str]:
    plans: set[str] = set()
    for item in tool_call_log or []:
        if not isinstance(item, dict) or item.get("success") is not True:
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        result = str(item.get("result_preview") or "")
        tool = str(item.get("tool") or "")

        if tool == "inspector_exec":
            if args.get("section") != "billing" or not args.get("tenant_id"):
                continue
            if "AUTHORITATIVE_BILLING_SOURCE=billing_db" not in result:
                continue
            plans.update(match.lower() for match in _INSPECTOR_PLAN_RE.findall(result))
            continue

        if tool == "sql_query":
            sql = str(args.get("sql") or "").lower()
            if str(args.get("database") or "").lower() != "billing":
                continue
            if "tenant_subscription_settings" not in sql or "tenant_id" not in sql:
                continue
            plans.update(_plans_from_billing_csv(result))
    return plans


def billing_plan_claim_block_reason(
    *,
    body: str,
    tool_call_log: list[dict[str, Any]],
) -> str | None:
    """Return a blocker unless a current-plan claim matches Billing evidence."""
    asserted = _asserted_current_plans(body)
    if not asserted:
        return None

    authoritative = _authoritative_plans(tool_call_log)
    if not authoritative:
        return (
            "Billing plan evidence gate blocked this public reply: a current "
            "subscription-plan claim requires tenant-scoped Billing evidence. "
            "Run inspector_exec with tenant_id + section=billing, or sql_query "
            "database=billing against tenant_subscription_settings. "
            "TENANT/TENANTS Plan and inspector email/section=all are not "
            "authoritative subscription evidence."
        )

    if not asserted.issubset(authoritative):
        return (
            "Billing plan evidence gate blocked this public reply: the reply "
            f"asserts {', '.join(sorted(asserted))}, while authoritative Billing "
            f"evidence returned {', '.join(sorted(authoritative))}. Regenerate "
            "the reply from the Billing result."
        )
    return None
