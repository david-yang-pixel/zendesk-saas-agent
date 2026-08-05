"""Fail-closed identity and evidence gate for subscription-plan claims."""

from __future__ import annotations

import csv
from dataclasses import dataclass
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
_TENANT_ID_RE: Final = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{2,127}$")


@dataclass(frozen=True)
class _WorkspaceResolution:
    status: str
    tenant_ids: frozenset[str]


def _normalize_email(value: object) -> str:
    return str(value or "").strip().casefold()


def _normalize_tenant_id(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _TENANT_ID_RE.fullmatch(candidate) else ""


def _asserted_current_plans(body: str) -> set[str]:
    plans: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", body or ""):
        if not _CURRENT_PLAN_CONTEXT_RE.search(sentence):
            continue
        plans.update(match.lower() for match in _PLAN_WITH_LABEL_RE.findall(sentence))
    return plans


def _plans_from_billing_csv(text: str) -> dict[str, set[str]]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        try:
            header = [cell.strip().lower() for cell in next(csv.reader([line]))]
        except (csv.Error, StopIteration):
            continue
        if "tenant_id" not in header or "plan" not in header:
            continue
        tenant_index = header.index("tenant_id")
        plan_index = header.index("plan")
        plans: dict[str, set[str]] = {}
        for row_line in lines[index + 1 :]:
            try:
                row = [cell.strip().lower() for cell in next(csv.reader([row_line]))]
            except (csv.Error, StopIteration):
                continue
            if len(row) <= max(tenant_index, plan_index):
                continue
            tenant_id = _normalize_tenant_id(row[tenant_index])
            plan = row[plan_index]
            if tenant_id and plan in _PLAN_CODES:
                plans.setdefault(tenant_id, set()).add(plan)
        return plans
    return {}


def _authoritative_plans_by_tenant(
    tool_call_log: list[dict[str, Any]],
) -> dict[str, set[str]]:
    plans: dict[str, set[str]] = {}
    for item in tool_call_log or []:
        if not isinstance(item, dict) or item.get("success") is not True:
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        result = str(item.get("result_preview") or "")
        tool = str(item.get("tool") or "")

        if tool == "inspector_exec":
            tenant_id = _normalize_tenant_id(args.get("tenant_id"))
            if args.get("section") != "billing" or not tenant_id:
                continue
            if "AUTHORITATIVE_BILLING_SOURCE=billing_db" not in result:
                continue
            current = {
                match.lower() for match in _INSPECTOR_PLAN_RE.findall(result)
            }
            if current:
                plans[tenant_id] = current
            continue

        if tool == "sql_query":
            sql = str(args.get("sql") or "").lower()
            if str(args.get("database") or "").lower() != "billing":
                continue
            if "tenant_subscription_settings" not in sql or "tenant_id" not in sql:
                continue
            plans.update(_plans_from_billing_csv(result))
    return plans


def _prefixed_value(text: str, key: str) -> str:
    match = re.search(
        rf"^{re.escape(key)}=([^\r\n]*)$",
        text,
        re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def _workspace_resolution_for_email(
    tool_call_log: list[dict[str, Any]],
    email: str,
) -> _WorkspaceResolution | None:
    expected_email = _normalize_email(email)
    if not expected_email:
        return None

    for item in reversed(tool_call_log or []):
        if not isinstance(item, dict) or item.get("success") is not True:
            continue
        if item.get("tool") != "inspector_exec":
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        if args.get("section") != "tenants":
            continue
        if _normalize_email(args.get("email")) != expected_email:
            continue

        result = str(item.get("result_preview") or "")
        if _prefixed_value(result, "EMAIL_WORKSPACE_SOURCE") != "prod_membership":
            return _WorkspaceResolution("unverified", frozenset())
        status = _prefixed_value(result, "EMAIL_WORKSPACE_RESOLUTION")
        raw_count = _prefixed_value(result, "EMAIL_WORKSPACE_COUNT")
        raw_ids = _prefixed_value(result, "EMAIL_WORKSPACE_TENANT_IDS")
        try:
            count = int(raw_count)
        except ValueError:
            return _WorkspaceResolution("unverified", frozenset())
        tenant_ids = frozenset(
            tenant_id
            for value in raw_ids.split(",")
            if (tenant_id := _normalize_tenant_id(value))
        )
        if count != len(tenant_ids):
            return _WorkspaceResolution("unverified", tenant_ids)
        if status not in {"unique", "ambiguous", "none"}:
            return _WorkspaceResolution("unverified", tenant_ids)
        if status == "unique" and count != 1:
            return _WorkspaceResolution("unverified", tenant_ids)
        if status == "ambiguous" and count < 2:
            return _WorkspaceResolution("unverified", tenant_ids)
        if status == "none" and count != 0:
            return _WorkspaceResolution("unverified", tenant_ids)
        return _WorkspaceResolution(status, tenant_ids)
    return None


def billing_plan_claim_block_reason(
    *,
    body: str,
    tool_call_log: list[dict[str, Any]],
    form_email: str = "",
    form_workspace_id: str = "",
    requester_email: str = "",
) -> str | None:
    """Return a blocker unless plan evidence is bound to the customer's workspace."""
    asserted = _asserted_current_plans(body)
    if not asserted:
        return None

    plans_by_tenant = _authoritative_plans_by_tenant(tool_call_log)
    if not plans_by_tenant:
        return (
            "Billing plan evidence gate blocked this public reply: a current "
            "subscription-plan claim requires tenant-scoped Billing evidence. "
            "Run inspector_exec with tenant_id + section=billing, or sql_query "
            "database=billing against tenant_subscription_settings. "
            "TENANT/TENANTS Plan and inspector email/section=all are not "
            "authoritative subscription evidence."
        )

    identity_email = _normalize_email(form_email) or _normalize_email(requester_email)
    identity_label = "form email" if _normalize_email(form_email) else "requester email"
    if not identity_email:
        return (
            "Workspace identity evidence gate blocked this public reply: no form "
            "email or requester email is available to resolve the customer workspace. "
            "Do not guess which tenant the Billing result belongs to."
        )

    resolution = _workspace_resolution_for_email(tool_call_log, identity_email)
    if resolution is None:
        return (
            "Workspace identity evidence gate blocked this public reply: resolve "
            f"the workspace from the {identity_label} first by running "
            "inspector_exec with email + section=tenants. Do not use requester "
            "metadata in place of an available form email."
        )
    if resolution.status in {"none", "unverified"}:
        return (
            "Workspace identity evidence gate blocked this public reply: the "
            f"{identity_label} did not return a verified workspace membership. "
            "Do not infer a tenant or subscription plan."
        )

    supplied_workspace_id = _normalize_tenant_id(form_workspace_id)
    if supplied_workspace_id and supplied_workspace_id not in resolution.tenant_ids:
        return (
            "Workspace identity evidence gate blocked this public reply: the form "
            "Workspace ID is not among the workspaces resolved from the form email. "
            "The target workspace must be clarified before checking its plan."
        )

    if resolution.status == "ambiguous":
        if not supplied_workspace_id:
            return (
                "Workspace identity evidence gate blocked this public reply: the "
                "form email maps to multiple workspaces and the form does not contain "
                "a matching Workspace ID. Ask the customer to identify the target; "
                "do not guess a tenant or plan."
            )
        resolved_tenant_id = supplied_workspace_id
    else:
        resolved_tenant_id = next(iter(resolution.tenant_ids))

    authoritative = plans_by_tenant.get(resolved_tenant_id, set())
    if not authoritative:
        return (
            "Workspace identity evidence gate blocked this public reply: the Billing "
            "evidence does not target the resolved workspace from the form email. "
            "Run the Billing lookup again with the resolved tenant_id."
        )

    if not asserted.issubset(authoritative):
        return (
            "Billing plan evidence gate blocked this public reply: the reply "
            f"asserts {', '.join(sorted(asserted))}, while authoritative Billing "
            f"evidence returned {', '.join(sorted(authoritative))}. Regenerate "
            "the reply from the Billing result."
        )
    return None
