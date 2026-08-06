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
_EMAIL_RE: Final = re.compile(
    r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    re.IGNORECASE,
)
_SUBSCRIPTION_RE: Final = re.compile(
    r"\b(?:paid[-\s]?subscription|subscription)\b",
    re.IGNORECASE,
)
_SUBSCRIPTION_STATE_RE: Final = re.compile(
    r"\b(?:active|inactive|cancel(?:ed|led)|expired|ended|past[_ -]?due|"
    r"no\s+longer\s+active|no\s+active|not\s+active|could\s+not\s+find|"
    r"did\s+not\s+find|does\s+not\s+have|has\s+no)\b",
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


def _email_candidate(value: object) -> str:
    candidate = _normalize_email(value)
    return candidate if _EMAIL_RE.fullmatch(candidate) else ""


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


def _asserts_current_subscription_state(body: str) -> bool:
    for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", body or ""):
        if not _CURRENT_PLAN_CONTEXT_RE.search(sentence):
            continue
        if _SUBSCRIPTION_RE.search(sentence) and _SUBSCRIPTION_STATE_RE.search(sentence):
            return True
    return False


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


def workspace_identity_candidates_block_reason(
    *,
    tool_call_log: list[dict[str, Any]],
    form_email: str = "",
    form_workspace_id: str = "",
) -> str | None:
    """Require support-side lookups for every email-shaped form identity candidate."""
    workspace_identity_email = _email_candidate(form_workspace_id)
    if not workspace_identity_email:
        return None

    target_resolution = _workspace_resolution_for_email(
        tool_call_log,
        workspace_identity_email,
    )
    if target_resolution is None:
        return (
            "Workspace identity evidence gate blocked this public reply: the "
            "form Workspace ID contains an email-shaped target identifier. "
            "Resolve that target first with inspector_exec using the Workspace "
            "ID email + section=tenants; do not reuse evidence from the form "
            "Email tenant."
        )
    if target_resolution.status == "unverified":
        return (
            "Workspace identity evidence gate blocked this public reply: the "
            "email-shaped form Workspace ID lookup was not verified. Repeat "
            "inspector_exec with the Workspace ID email + section=tenants."
        )

    form_identity_email = _email_candidate(form_email)
    if form_identity_email and form_identity_email != workspace_identity_email:
        contact_resolution = _workspace_resolution_for_email(
            tool_call_log,
            form_identity_email,
        )
        if contact_resolution is None:
            return (
                "Workspace identity evidence gate blocked this public reply: the "
                "form Email and the email provided in form Workspace ID are "
                "different. Cross-check the form Email with inspector_exec "
                "email + section=tenants before replying."
            )
        if contact_resolution.status == "unverified":
            return (
                "Workspace identity evidence gate blocked this public reply: the "
                "form Email workspace lookup was not verified. Repeat "
                "inspector_exec with email + section=tenants."
            )
    return None


def resolved_target_tenant_id(
    *,
    tool_call_log: list[dict[str, Any]],
    form_email: str = "",
    form_workspace_id: str = "",
    requester_email: str = "",
) -> str:
    """Return the uniquely resolved target tenant selected by the form identity."""
    workspace_identity_email = _email_candidate(form_workspace_id)
    identity_email = (
        workspace_identity_email
        or _email_candidate(form_email)
        or _email_candidate(requester_email)
    )
    resolution = _workspace_resolution_for_email(tool_call_log, identity_email)
    if resolution is None or resolution.status not in {"unique", "ambiguous"}:
        return ""

    supplied_workspace_id = (
        "" if workspace_identity_email else _normalize_tenant_id(form_workspace_id)
    )
    if supplied_workspace_id:
        return (
            supplied_workspace_id
            if supplied_workspace_id in resolution.tenant_ids
            else ""
        )
    if resolution.status == "unique":
        return next(iter(resolution.tenant_ids))
    return ""


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
    subscription_state_assertion = _asserts_current_subscription_state(body)
    if not asserted and not subscription_state_assertion:
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

    form_identity_email = _email_candidate(form_email)
    workspace_identity_email = _email_candidate(form_workspace_id)
    requester_identity_email = _email_candidate(requester_email)
    identity_email = (
        workspace_identity_email
        or form_identity_email
        or requester_identity_email
    )
    if workspace_identity_email:
        identity_label = "email provided in the form Workspace ID"
    elif form_identity_email:
        identity_label = "form email"
    else:
        identity_label = "requester email"
    if not identity_email:
        return (
            "Workspace identity evidence gate blocked this public reply: no form "
            "email or requester email is available to resolve the customer workspace. "
            "Do not guess which tenant the Billing result belongs to."
        )

    candidate_block = workspace_identity_candidates_block_reason(
        tool_call_log=tool_call_log,
        form_email=form_email,
        form_workspace_id=form_workspace_id,
    )
    if candidate_block:
        return candidate_block

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

    supplied_workspace_id = (
        "" if workspace_identity_email else _normalize_tenant_id(form_workspace_id)
    )
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
                f"{identity_label} maps to multiple workspaces and the form does "
                "not contain a matching tenant ID. Ask the customer to identify "
                "the target; do not guess a tenant or plan."
            )
        resolved_tenant_id = supplied_workspace_id
    else:
        resolved_tenant_id = next(iter(resolution.tenant_ids))

    authoritative = plans_by_tenant.get(resolved_tenant_id, set())
    if not authoritative:
        return (
            "Workspace identity evidence gate blocked this public reply: the Billing "
            f"evidence does not target the resolved workspace from the {identity_label}. "
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
