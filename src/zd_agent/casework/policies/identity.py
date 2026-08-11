from __future__ import annotations

import re
import unicodedata

from zd_agent.casework.domain.enums import (
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
) -> IdentityResolution:
    return IdentityResolution(
        contact_email=contact_email,
        target_identity=target_identity,
        status=status,
        candidates=candidates,
        evidence_ids=evidence_ids,
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


def resolve_workspace_identity(
    *,
    form_email: str,
    form_workspace_id: str,
    requester_email: str,
    lookups: tuple[WorkspaceMembershipLookup, ...],
    form_evidence_id: str,
) -> IdentityResolution:
    """Resolve the target workspace without turning internal failures into asks."""
    normalized_form_email = _email(form_email)
    normalized_requester_email = _email(requester_email)
    contact_email = normalized_form_email or normalized_requester_email
    workspace_email = _email(form_workspace_id)
    canonical_id = "" if workspace_email else _canonical_tenant_id(form_workspace_id)
    display_name = "" if workspace_email or canonical_id else form_workspace_id
    target_email = workspace_email or contact_email
    target_identity = workspace_email or canonical_id or display_name.strip() or contact_email

    lookup_by_email = {lookup.email: lookup for lookup in lookups}
    relevant: list[WorkspaceMembershipLookup] = []
    required_emails = tuple(
        dict.fromkeys(
            email
            for email in (target_email, contact_email if workspace_email else "")
            if email
        ),
    )
    for email in required_emails:
        lookup = lookup_by_email.get(email)
        if lookup is None:
            return _unresolved(
                contact_email=contact_email,
                target_identity=target_identity,
                status=ResolutionStatus.LOOKUP_PENDING,
                candidates=(),
                evidence_ids=_evidence_ids(
                    form_evidence_id=form_evidence_id,
                    lookups=tuple(relevant),
                ),
                detail=f"support-side {('contact' if email == contact_email else 'target')} lookup pending",
            )
        relevant.append(lookup)
        if lookup.status is LookupStatus.FAILED:
            return _unresolved(
                contact_email=contact_email,
                target_identity=target_identity,
                status=ResolutionStatus.LOOKUP_FAILED,
                candidates=(),
                evidence_ids=_evidence_ids(
                    form_evidence_id=form_evidence_id,
                    lookups=tuple(relevant),
                ),
                detail=f"support-side lookup failed: {lookup.error_code.value}",
            )
        if lookup.status is LookupStatus.PENDING:
            return _unresolved(
                contact_email=contact_email,
                target_identity=target_identity,
                status=ResolutionStatus.LOOKUP_PENDING,
                candidates=(),
                evidence_ids=_evidence_ids(
                    form_evidence_id=form_evidence_id,
                    lookups=tuple(relevant),
                ),
                detail="support-side lookup pending",
            )

    evidence_ids = _evidence_ids(
        form_evidence_id=form_evidence_id,
        lookups=tuple(relevant),
    )
    if not target_email:
        if canonical_id:
            candidate = WorkspaceCandidate(tenant_id=canonical_id)
            return _resolved(
                contact_email=contact_email,
                target_identity=target_identity,
                candidate=candidate,
                candidates=(candidate,),
                method=ResolutionMethod.EXPLICIT_CANONICAL_ID,
                evidence_ids=evidence_ids,
            )
        return _unresolved(
            contact_email=contact_email,
            target_identity=target_identity,
            status=ResolutionStatus.NO_MEMBERSHIP,
            candidates=(),
            evidence_ids=evidence_ids,
            detail="no usable support-side identity candidate",
        )

    target_lookup = lookup_by_email[target_email]
    candidates = target_lookup.candidates
    if not candidates:
        status = ResolutionStatus.CONFLICT if canonical_id else ResolutionStatus.NO_MEMBERSHIP
        return _unresolved(
            contact_email=contact_email,
            target_identity=target_identity,
            status=status,
            candidates=(),
            evidence_ids=evidence_ids,
            detail="successful membership lookup returned no target workspace",
        )

    if canonical_id:
        matching = next(
            (candidate for candidate in candidates if candidate.tenant_id == canonical_id),
            None,
        )
        if matching is None:
            return _unresolved(
                contact_email=contact_email,
                target_identity=target_identity,
                status=ResolutionStatus.CONFLICT,
                candidates=candidates,
                evidence_ids=evidence_ids,
                detail="canonical Workspace ID is not among the email memberships",
            )
        return _resolved(
            contact_email=contact_email,
            target_identity=target_identity,
            candidate=matching,
            candidates=candidates,
            method=ResolutionMethod.EXPLICIT_CANONICAL_ID,
            evidence_ids=evidence_ids,
        )

    if len(candidates) == 1:
        return _resolved(
            contact_email=contact_email,
            target_identity=target_identity,
            candidate=candidates[0],
            candidates=candidates,
            method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
            evidence_ids=evidence_ids,
        )

    normalized_name = _workspace_name(display_name)
    matches = tuple(
        candidate
        for candidate in candidates
        if normalized_name
        and _workspace_name(candidate.workspace_name) == normalized_name
    )
    if len(matches) == 1:
        return _resolved(
            contact_email=contact_email,
            target_identity=target_identity,
            candidate=matches[0],
            candidates=candidates,
            method=ResolutionMethod.UNIQUE_EXACT_CANDIDATE_NAME,
            evidence_ids=evidence_ids,
        )
    return _unresolved(
        contact_email=contact_email,
        target_identity=target_identity,
        status=ResolutionStatus.AMBIGUOUS,
        candidates=candidates,
        evidence_ids=evidence_ids,
        detail="multiple workspace memberships require customer-controlled disambiguation",
    )
