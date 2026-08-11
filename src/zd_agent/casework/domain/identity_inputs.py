from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", re.IGNORECASE)
_CANONICAL_TENANT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class IdentityInputs:
    contact_email: str
    workspace_email: str
    canonical_id: str
    display_name: str
    customer_display_name: str
    target_email: str
    target_identity: str
    required_emails: tuple[str, ...]


def validated_email(value: str) -> str:
    candidate = value.strip().casefold()
    return candidate if _EMAIL_RE.fullmatch(candidate) else ""


def workspace_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def identity_lookup_emails(
    *,
    form_email: str,
    form_workspace_id: str,
    requester_email: str,
    customer_workspace_hint: str = "",
) -> tuple[str, ...]:
    """Return every support-side membership lookup required by form precedence."""
    contact_email = validated_email(form_email) or validated_email(requester_email)
    workspace_email = validated_email(customer_workspace_hint) or validated_email(
        form_workspace_id
    )
    return tuple(
        dict.fromkeys(
            email
            for email in (workspace_email or contact_email, contact_email)
            if email
        )
    )


def normalize_identity_inputs(
    *,
    form_email: str,
    form_workspace_id: str,
    requester_email: str,
    customer_workspace_hint: str,
) -> IdentityInputs:
    contact_email = validated_email(form_email) or validated_email(requester_email)
    customer_email = validated_email(customer_workspace_hint)
    workspace_email = customer_email or validated_email(form_workspace_id)
    raw_tenant_id = form_workspace_id.strip().casefold()
    canonical_id = (
        ""
        if workspace_email
        else raw_tenant_id
        if _CANONICAL_TENANT_ID_RE.fullmatch(raw_tenant_id)
        else ""
    )
    display_name = "" if workspace_email or canonical_id else form_workspace_id
    customer_display_name = "" if customer_email else customer_workspace_hint.strip()
    target_email = workspace_email or contact_email
    target_identity = (
        workspace_email or canonical_id or display_name.strip() or contact_email
    )
    required_emails = tuple(
        dict.fromkeys(
            email
            for email in (target_email, contact_email if workspace_email else "")
            if email
        )
    )
    return IdentityInputs(
        contact_email=contact_email,
        workspace_email=workspace_email,
        canonical_id=canonical_id,
        display_name=display_name,
        customer_display_name=customer_display_name,
        target_email=target_email,
        target_identity=target_identity,
        required_emails=required_emails,
    )
