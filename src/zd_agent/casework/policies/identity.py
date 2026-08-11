from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    LookupStatus,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    IdentityResolution,
    WorkspaceCandidate,
    WorkspaceMembershipLookup,
)


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", re.IGNORECASE)
_CANONICAL_TENANT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _IdentityInputs:
    contact_email: str
    workspace_email: str
    canonical_id: str
    display_name: str
    target_email: str
    target_identity: str
    required_emails: tuple[str, ...]


def _email(value: str) -> str:
    candidate = value.strip().casefold()
    return candidate if _EMAIL_RE.fullmatch(candidate) else ""


def _canonical_tenant_id(value: str) -> str:
    candidate = value.strip().casefold()
    return candidate if _CANONICAL_TENANT_ID_RE.fullmatch(candidate) else ""


def _workspace_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def identity_lookup_emails(
    *,
    form_email: str,
    form_workspace_id: str,
    requester_email: str,
) -> tuple[str, ...]:
    """Return every support-side membership lookup required by form precedence."""
    contact_email = _email(form_email) or _email(requester_email)
    workspace_email = _email(form_workspace_id)
    return tuple(
        dict.fromkeys(
            email
            for email in (workspace_email or contact_email, contact_email)
            if email
        ),
    )


def _evidence_ids(
    *,
    form_evidence_id: str,
    lookups: tuple[WorkspaceMembershipLookup, ...],
) -> tuple[str, ...]:
    values = [form_evidence_id]
    values.extend(lookup.evidence_id for lookup in lookups if lookup.evidence_id)
    return tuple(dict.fromkeys(value for value in values if value))


def _unresolved(
    *,
    contact_email: str,
    target_identity: str,
    status: ResolutionStatus,
    candidates: tuple[WorkspaceCandidate, ...],
    evidence_ids: tuple[str, ...],
    detail: str,
    error_code: AdapterErrorCode | None = None,
) -> IdentityResolution:
    return IdentityResolution(
        contact_email=contact_email,
        target_identity=target_identity,
        status=status,
        candidates=candidates,
        evidence_ids=evidence_ids,
        error_code=error_code,
        detail=detail,
    )


def _resolved(
    *,
    contact_email: str,
    target_identity: str,
    candidate: WorkspaceCandidate,
    candidates: tuple[WorkspaceCandidate, ...],
    method: ResolutionMethod,
    evidence_ids: tuple[str, ...],
) -> IdentityResolution:
    return IdentityResolution(
        contact_email=contact_email,
        target_identity=target_identity,
        status=ResolutionStatus.RESOLVED,
        method=method,
        candidates=candidates,
        selected_tenant_id=candidate.tenant_id,
        selected_workspace_name=candidate.workspace_name,
        selected_role=candidate.role,
        evidence_ids=evidence_ids,
    )


def _normalize_inputs(
    *,
    form_email: str,
    form_workspace_id: str,
    requester_email: str,
) -> _IdentityInputs:
    contact_email = _email(form_email) or _email(requester_email)
    workspace_email = _email(form_workspace_id)
    canonical_id = "" if workspace_email else _canonical_tenant_id(form_workspace_id)
    display_name = "" if workspace_email or canonical_id else form_workspace_id
    target_email = workspace_email or contact_email
    target_identity = workspace_email or canonical_id or display_name.strip() or contact_email
    required_emails = tuple(
        dict.fromkeys(
            email
            for email in (target_email, contact_email if workspace_email else "")
            if email
        ),
    )
    return _IdentityInputs(
        contact_email=contact_email,
        workspace_email=workspace_email,
        canonical_id=canonical_id,
        display_name=display_name,
        target_email=target_email,
        target_identity=target_identity,
        required_emails=required_emails,
    )


def _lookup_problem(
    *,
    inputs: _IdentityInputs,
    email: str,
    lookup: WorkspaceMembershipLookup | None,
    relevant: tuple[WorkspaceMembershipLookup, ...],
    form_evidence_id: str,
) -> IdentityResolution | None:
    if lookup is None:
        kind = "contact" if email == inputs.contact_email else "target"
        return _unresolved(
            contact_email=inputs.contact_email,
            target_identity=inputs.target_identity,
            status=ResolutionStatus.LOOKUP_PENDING,
            candidates=(),
            evidence_ids=_evidence_ids(
                form_evidence_id=form_evidence_id,
                lookups=relevant,
            ),
            detail=f"support-side {kind} lookup pending",
        )
    checked = (*relevant, lookup)
    if lookup.status is LookupStatus.FAILED:
        error_code = lookup.error_code or AdapterErrorCode.PERMANENT_FAILURE
        return _unresolved(
            contact_email=inputs.contact_email,
            target_identity=inputs.target_identity,
            status=ResolutionStatus.LOOKUP_FAILED,
            candidates=(),
            evidence_ids=_evidence_ids(
                form_evidence_id=form_evidence_id,
                lookups=checked,
            ),
            detail=f"support-side lookup failed: {error_code.value}",
            error_code=error_code,
        )
    if lookup.status is LookupStatus.PENDING:
        return _unresolved(
            contact_email=inputs.contact_email,
            target_identity=inputs.target_identity,
            status=ResolutionStatus.LOOKUP_PENDING,
            candidates=(),
            evidence_ids=_evidence_ids(
                form_evidence_id=form_evidence_id,
                lookups=checked,
            ),
            detail="support-side lookup pending",
        )
    return None


def _lookup_gate(
    *,
    inputs: _IdentityInputs,
    lookups: tuple[WorkspaceMembershipLookup, ...],
    form_evidence_id: str,
) -> tuple[
    dict[str, WorkspaceMembershipLookup],
    tuple[WorkspaceMembershipLookup, ...],
    IdentityResolution | None,
]:
    lookup_by_email = {lookup.email: lookup for lookup in lookups}
    relevant: list[WorkspaceMembershipLookup] = []
    for email in inputs.required_emails:
        lookup = lookup_by_email.get(email)
        problem = _lookup_problem(
            inputs=inputs,
            email=email,
            lookup=lookup,
            relevant=tuple(relevant),
            form_evidence_id=form_evidence_id,
        )
        if problem is not None:
            return lookup_by_email, tuple(relevant), problem
        if lookup is not None:
            relevant.append(lookup)
    return lookup_by_email, tuple(relevant), None


def _resolve_without_target_email(
    inputs: _IdentityInputs,
    *,
    evidence_ids: tuple[str, ...],
) -> IdentityResolution:
    return _unresolved(
        contact_email=inputs.contact_email,
        target_identity=inputs.target_identity,
        status=ResolutionStatus.NO_MEMBERSHIP,
        candidates=(),
        evidence_ids=evidence_ids,
        detail="canonical identity requires verified email membership evidence",
    )


def _resolve_canonical_candidate(
    inputs: _IdentityInputs,
    candidates: tuple[WorkspaceCandidate, ...],
    *,
    evidence_ids: tuple[str, ...],
) -> IdentityResolution:
    matching = next(
        (candidate for candidate in candidates if candidate.tenant_id == inputs.canonical_id),
        None,
    )
    if matching is None:
        return _unresolved(
            contact_email=inputs.contact_email,
            target_identity=inputs.target_identity,
            status=ResolutionStatus.CONFLICT,
            candidates=candidates,
            evidence_ids=evidence_ids,
            detail="canonical Workspace ID is not among the email memberships",
        )
    return _resolved(
        contact_email=inputs.contact_email,
        target_identity=inputs.target_identity,
        candidate=matching,
        candidates=candidates,
        method=ResolutionMethod.EXPLICIT_CANONICAL_ID,
        evidence_ids=evidence_ids,
    )


def _resolve_multiple_candidates(
    inputs: _IdentityInputs,
    candidates: tuple[WorkspaceCandidate, ...],
    *,
    evidence_ids: tuple[str, ...],
) -> IdentityResolution:
    normalized_name = _workspace_name(inputs.display_name)
    matches = tuple(
        candidate
        for candidate in candidates
        if normalized_name and _workspace_name(candidate.workspace_name) == normalized_name
    )
    if len(matches) == 1:
        return _resolved(
            contact_email=inputs.contact_email,
            target_identity=inputs.target_identity,
            candidate=matches[0],
            candidates=candidates,
            method=ResolutionMethod.UNIQUE_EXACT_CANDIDATE_NAME,
            evidence_ids=evidence_ids,
        )
    return _unresolved(
        contact_email=inputs.contact_email,
        target_identity=inputs.target_identity,
        status=ResolutionStatus.AMBIGUOUS,
        candidates=candidates,
        evidence_ids=evidence_ids,
        detail="multiple workspace memberships require customer-controlled disambiguation",
    )


def _resolve_candidates(
    inputs: _IdentityInputs,
    candidates: tuple[WorkspaceCandidate, ...],
    *,
    evidence_ids: tuple[str, ...],
) -> IdentityResolution:
    if not candidates:
        return _unresolved(
            contact_email=inputs.contact_email,
            target_identity=inputs.target_identity,
            status=(
                ResolutionStatus.CONFLICT
                if inputs.canonical_id
                else ResolutionStatus.NO_MEMBERSHIP
            ),
            candidates=(),
            evidence_ids=evidence_ids,
            detail="successful membership lookup returned no target workspace",
        )
    if inputs.canonical_id:
        return _resolve_canonical_candidate(inputs, candidates, evidence_ids=evidence_ids)
    if len(candidates) == 1:
        return _resolved(
            contact_email=inputs.contact_email,
            target_identity=inputs.target_identity,
            candidate=candidates[0],
            candidates=candidates,
            method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
            evidence_ids=evidence_ids,
        )
    return _resolve_multiple_candidates(inputs, candidates, evidence_ids=evidence_ids)


def resolve_workspace_identity(
    *,
    form_email: str,
    form_workspace_id: str,
    requester_email: str,
    lookups: tuple[WorkspaceMembershipLookup, ...],
    form_evidence_id: str,
) -> IdentityResolution:
    """Resolve the target workspace without turning internal failures into asks."""
    inputs = _normalize_inputs(
        form_email=form_email,
        form_workspace_id=form_workspace_id,
        requester_email=requester_email,
    )
    lookup_by_email, relevant, unresolved = _lookup_gate(
        inputs=inputs,
        lookups=lookups,
        form_evidence_id=form_evidence_id,
    )
    if unresolved is not None:
        return unresolved
    evidence_ids = _evidence_ids(
        form_evidence_id=form_evidence_id,
        lookups=relevant,
    )
    if not inputs.target_email:
        return _resolve_without_target_email(inputs, evidence_ids=evidence_ids)
    candidates = lookup_by_email[inputs.target_email].candidates
    return _resolve_candidates(inputs, candidates, evidence_ids=evidence_ids)
