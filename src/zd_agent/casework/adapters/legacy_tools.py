from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Protocol, cast
from uuid import UUID
from uuid import uuid4

from zd_agent.casework.domain.enums import AdapterErrorCode, LookupStatus
from zd_agent.casework.domain.models import (
    BillingLookup,
    WorkspaceCandidate,
    WorkspaceMembershipLookup,
)
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry


@dataclass(frozen=True, slots=True)
class LegacyToolObservation:
    call_id: str
    success: bool
    output: str = ""
    error: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


class LegacyToolCaller(Protocol):
    async def call(
        self,
        tool_name: str,
        args: dict[str, object],
    ) -> LegacyToolObservation: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class LegacySessionToolCaller:
    """Execute only existing read-only tools for a no-write shadow run."""

    def __init__(self, registry: ToolRegistry, session: AgentSession) -> None:
        self._registry = registry
        self._session = session

    async def call(
        self,
        tool_name: str,
        args: dict[str, object],
    ) -> LegacyToolObservation:
        call_id = f"shadow-{uuid4().hex}"
        tool = self._registry.get(tool_name)
        if tool is None:
            return LegacyToolObservation(
                call_id=call_id,
                success=False,
                error=f"unknown read tool: {tool_name}",
            )
        if tool.is_write_operation:
            return LegacyToolObservation(
                call_id=call_id,
                success=False,
                error=f"shadow mode blocked write tool: {tool_name}",
            )
        try:
            result = await tool.execute(cast(dict[str, Any], args), self._session)
        except Exception as error:
            return LegacyToolObservation(
                call_id=call_id,
                success=False,
                error=f"{type(error).__name__}: {error}",
            )
        return LegacyToolObservation(
            call_id=call_id,
            success=result.success,
            output=result.output,
            error=result.error or "",
            metadata=cast(dict[str, object], result.metadata),
        )


def _error_code(detail: str) -> AdapterErrorCode:
    lowered = detail.casefold()
    markers = (
        (("timeout", "timed out", "超时"), AdapterErrorCode.TIMEOUT),
        (("rate limit", "rate_limited", "429"), AdapterErrorCode.RATE_LIMITED),
        (("auth", "credential", "token expired"), AdapterErrorCode.AUTH_EXPIRED),
        (("permission denied", "forbidden", "403"), AdapterErrorCode.PERMISSION_DENIED),
        (("not found", "no record", "404"), AdapterErrorCode.NOT_FOUND),
        (("invalid", "bad request", "400"), AdapterErrorCode.INVALID_REQUEST),
        (("connection", "transport", "unavailable"), AdapterErrorCode.TRANSPORT_UNAVAILABLE),
    )
    return next(
        (code for values, code in markers if any(value in lowered for value in values)),
        AdapterErrorCode.PERMANENT_FAILURE,
    )


def _failed_membership(
    email: str,
    observation: LegacyToolObservation,
    code: AdapterErrorCode,
) -> WorkspaceMembershipLookup:
    return WorkspaceMembershipLookup(
        email=email,
        status=LookupStatus.FAILED,
        evidence_id=observation.call_id,
        error_code=code,
    )


def _membership_candidates(payload: dict[str, object]) -> tuple[WorkspaceCandidate, ...] | None:
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        return None
    candidates: list[WorkspaceCandidate] = []
    for value in raw_candidates:
        if not isinstance(value, dict):
            return None
        tenant_id = value.get("tenant_id")
        name = value.get("name")
        role = value.get("role", "")
        if not isinstance(tenant_id, str) or not isinstance(name, str):
            return None
        if not isinstance(role, str):
            return None
        try:
            canonical_id = str(UUID(tenant_id))
        except ValueError:
            return None
        if canonical_id != tenant_id.casefold():
            return None
        candidates.append(
            WorkspaceCandidate(
                tenant_id=canonical_id,
                workspace_name=name,
                role=role,
            ),
        )
    return tuple(candidates)


def _validated_memberships(metadata: dict[str, object]) -> tuple[WorkspaceCandidate, ...] | None:
    raw_payload = metadata.get("workspace_resolution")
    if not isinstance(raw_payload, dict):
        return None
    payload = cast(dict[str, object], raw_payload)
    status = payload.get("status")
    count = payload.get("count")
    raw_tenant_ids = payload.get("tenant_ids")
    candidates = _membership_candidates(payload)
    if (
        payload.get("source") != "prod_membership"
        or candidates is None
        or not isinstance(raw_tenant_ids, list)
        or not all(isinstance(value, str) for value in raw_tenant_ids)
    ):
        return None
    tenant_ids = tuple(cast(str, value).casefold() for value in raw_tenant_ids)
    if tenant_ids != tuple(candidate.tenant_id for candidate in candidates):
        return None
    if not isinstance(count, int) or isinstance(count, bool) or count != len(candidates):
        return None
    if len({candidate.tenant_id for candidate in candidates}) != len(candidates):
        return None
    valid_shape = (
        (status == "none" and count == 0)
        or (status == "unique" and count == 1)
        or (status == "ambiguous" and count >= 2)
    )
    return candidates if valid_shape else None


class InspectorIdentityReader:
    def __init__(self, caller: LegacyToolCaller) -> None:
        self._caller = caller

    async def find_workspaces(self, email: str) -> WorkspaceMembershipLookup:
        normalized = email.strip().casefold()
        observation = await self._caller.call(
            "inspector_exec",
            {"email": normalized, "section": "tenants"},
        )
        if not observation.success:
            return _failed_membership(
                normalized,
                observation,
                _error_code(observation.error),
            )
        candidates = _validated_memberships(observation.metadata)
        if candidates is None or not observation.call_id:
            return _failed_membership(
                normalized,
                observation,
                AdapterErrorCode.MALFORMED_RESPONSE,
            )
        return WorkspaceMembershipLookup(
            email=normalized,
            status=LookupStatus.SUCCESS,
            candidates=candidates,
            evidence_id=observation.call_id,
        )


_PREFIX_RE = re.compile(r"^([A-Z_]+)=([^\r\n]*)$", re.MULTILINE)


def _prefixed_values(output: str) -> dict[str, str]:
    return {match.group(1): match.group(2).strip() for match in _PREFIX_RE.finditer(output)}


def _pipe_value(output: str, field_name: str) -> str:
    pattern = re.compile(
        rf"^\|\s*{re.escape(field_name)}\s*\|\s*([^|]+?)\s*\|\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(output)
    return match.group(1).strip() if match else ""


def _failed_billing(
    tenant_id: str,
    observation: LegacyToolObservation,
    *,
    observed_at: datetime,
    code: AdapterErrorCode,
) -> BillingLookup:
    return BillingLookup(
        tenant_id=tenant_id,
        status=LookupStatus.FAILED,
        observed_at=observed_at,
        evidence_id=observation.call_id,
        error_code=code,
    )


class InspectorBillingReader:
    def __init__(
        self,
        caller: LegacyToolCaller,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._caller = caller
        self._now = now

    async def read_billing(self, tenant_id: str) -> BillingLookup:
        observation = await self._caller.call(
            "inspector_exec",
            {"tenant_id": tenant_id, "section": "billing"},
        )
        observed_at = self._now()
        if not observation.success:
            return _failed_billing(
                tenant_id,
                observation,
                observed_at=observed_at,
                code=_error_code(observation.error),
            )
        values = _prefixed_values(observation.output)
        authoritative = (
            values.get("AUTHORITATIVE_BILLING_SOURCE") == "billing_db"
            and values.get("AUTHORITATIVE_BILLING_PLAN") not in {None, "", "unknown"}
            and bool(observation.call_id)
        )
        if not authoritative:
            return _failed_billing(
                tenant_id,
                observation,
                observed_at=observed_at,
                code=AdapterErrorCode.MALFORMED_RESPONSE,
            )
        return BillingLookup(
            tenant_id=tenant_id,
            status=LookupStatus.SUCCESS,
            observed_at=observed_at,
            evidence_id=observation.call_id,
            plan=values["AUTHORITATIVE_BILLING_PLAN"],
            subscription_status=values.get("AUTHORITATIVE_BILLING_STATUS", ""),
            interval=_pipe_value(observation.output, "Billing Interval"),
            stripe_status=_pipe_value(observation.output, "Stripe Status"),
            entitlement_status=_pipe_value(observation.output, "Entitlement Status"),
            cancellation_status=_pipe_value(observation.output, "Cancellation Status"),
            renewal_status=_pipe_value(observation.output, "Renewal Status"),
        )
