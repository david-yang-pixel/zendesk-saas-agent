from __future__ import annotations

from zd_agent.casework.domain.enums import ResolutionStatus


_IDENTITY_FIELDS = frozenset(
    {
        "email",
        "workspace_id",
        "workspace_name",
        "target_workspace_choice",
        "target_workspace_identity",
    },
)
_CUSTOMER_ONLY_FIELDS = frozenset(
    {
        "reproduction_steps",
        "request_timestamp",
        "affected_object_id",
        "expected_behavior",
        "actual_behavior",
        "attachment_resend",
        "customer_authorization",
    },
)


def admit_customer_fields(
    *,
    proposed_fields: tuple[str, ...],
    identity_status: ResolutionStatus,
    attachment_resend_allowed: bool = True,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    proposed = tuple(dict.fromkeys(proposed_fields))
    approved = [
        field
        for field in proposed
        if field in _CUSTOMER_ONLY_FIELDS
        and (field != "attachment_resend" or attachment_resend_allowed)
    ]
    rejected = [
        field
        for field in proposed
        if (
            field not in _CUSTOMER_ONLY_FIELDS
            and field not in _IDENTITY_FIELDS
        )
        or (field == "attachment_resend" and not attachment_resend_allowed)
    ]
    identity_fields = tuple(field for field in proposed if field in _IDENTITY_FIELDS)
    if not identity_fields:
        return tuple(approved), tuple(rejected)
    rejected.extend(identity_fields)
    if identity_status in {ResolutionStatus.AMBIGUOUS, ResolutionStatus.CONFLICT}:
        approved.append("target_workspace_choice")
    elif identity_status is ResolutionStatus.NO_MEMBERSHIP:
        approved.append("target_workspace_identity")
    return tuple(dict.fromkeys(approved)), tuple(dict.fromkeys(rejected))
