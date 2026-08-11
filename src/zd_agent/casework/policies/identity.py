from __future__ import annotations

from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    LookupStatus,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.identity_inputs import (
    IdentityInputs,
    identity_lookup_emails as identity_lookup_emails,
    normalize_identity_inputs,
    workspace_name,
)
from zd_agent.casework.domain.models import (
    IdentityResolution,
    WorkspaceCandidate,
    WorkspaceMembershipLookup,
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


def _lookup_problem(
    *,
    inputs: IdentityInputs,
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
    inputs: IdentityInputs,
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
    inputs: IdentityInputs,
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
    inputs: IdentityInputs,
    candidates: tuple[WorkspaceCandidate, ...],
    *,
    evidence_ids: tuple[str, ...],
) -> IdentityResolution:
    matching = next(
        (
            candidate
            for candidate in candidates
            if candidate.tenant_id == inputs.canonical_id
        ),
        None,
    )
    if matching is None:
        customer_name = workspace_name(inputs.customer_display_name)
        customer_matches = tuple(
            candidate
            for candidate in candidates
            if customer_name
            and workspace_name(candidate.workspace_name) == customer_name
        )
        if len(customer_matches) == 1:
            return _resolved(
                contact_email=inputs.contact_email,
                target_identity=inputs.target_identity,
                candidate=customer_matches[0],
                candidates=candidates,
                method=ResolutionMethod.UNIQUE_EXACT_CANDIDATE_NAME,
                evidence_ids=evidence_ids,
            )
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
    inputs: IdentityInputs,
    candidates: tuple[WorkspaceCandidate, ...],
    *,
    evidence_ids: tuple[str, ...],
) -> IdentityResolution:
    normalized_names = tuple(
        dict.fromkeys(
            name
            for name in (
                workspace_name(inputs.display_name),
                workspace_name(inputs.customer_display_name),
            )
            if name
        ),
    )
    matches = tuple(
        candidate
        for candidate in candidates
        if workspace_name(candidate.workspace_name) in normalized_names
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
    inputs: IdentityInputs,
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
        return _resolve_canonical_candidate(
            inputs, candidates, evidence_ids=evidence_ids
        )
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
    customer_workspace_hint: str = "",
) -> IdentityResolution:
    """Resolve the target workspace without turning internal failures into asks."""
    inputs = normalize_identity_inputs(
        form_email=form_email,
        form_workspace_id=form_workspace_id,
        requester_email=requester_email,
        customer_workspace_hint=customer_workspace_hint,
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
