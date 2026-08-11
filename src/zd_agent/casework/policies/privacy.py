from __future__ import annotations

import re


_INTERNAL_DETAIL_RE = re.compile(
    r"\b(?:tenant_id|source_call_ids?|sql_query|inspector_exec|signoz|mcp__|"
    r"evidence ledger|database table|internal trace)\b|"
    r"/opt/zd-agent/|src/zd_agent/|\bSELECT\s+.+\s+FROM\b",
    re.IGNORECASE | re.DOTALL,
)

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"(?<![0-9a-f])(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})(?![0-9a-f])",
    re.IGNORECASE,
)
_LONG_HEX_ID_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{24,64}(?![0-9a-f])", re.IGNORECASE)
_SHORT_TRACE_ID_RE = re.compile(
    r"\b(?:trace|span|request|message|invocation)(?:\s+|-|_)?(?:id\s+)?(?:is\s+)?"
    r"[0-9a-f]{16,64}\b",
    re.IGNORECASE,
)
_TENANT_IDENTIFIER_RE = re.compile(r"\btenant(?:\s+|-|_)?id\b", re.IGNORECASE)
_PRIVATE_ADDRESS_RE = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b",
)
_INTERNAL_HOST_RE = re.compile(
    r"\b[a-z0-9][a-z0-9.-]*\.(?:svc\.cluster\.local|cluster\.local|"
    r"compute\.internal|internal)\b",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"(?:api[ _-]?key|password|secret|access[ _-]?token|credential)\s*"
    r"(?:is|=|:)\s*\S+)",
    re.IGNORECASE,
)
_ACTIVE_MARKUP_RE = re.compile(
    r"<\/?(?:at|font|a|script|style)\b[^>]*>|"
    r"\[[^\]\n]+\]\((?:javascript:|data:)[^)]+\)",
    re.IGNORECASE,
)
_KUBERNETES_POD_RE = re.compile(
    r"\b[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?-[0-9a-f]{8,10}-[a-z0-9]{5}\b",
    re.IGNORECASE,
)
_CLAIM_REQUEST_RE = re.compile(
    r"(?:^\s*(?:(?:please|kindly)\b|(?:can|could|would|will)\s+you\b|"
    r"(?:tell|share|provide|send|upload|attach|confirm|name)\b|"
    r"what\s+is\b|which\b|(?:i|we)\s+need\s+your\b)|"
    r"\blet\s+us\s+know\b|\byou\s+must\s+"
    r"(?:share|provide|send|upload|attach|confirm)\b|"
    r"\bit\s+would\s+help\s+if\s+you\s+"
    r"(?:provide|provided|share|shared|send|sent|upload|uploaded|attach|attached)\b|"
    r"\byour\s+next\s+step\s+is\s+to\s+"
    r"(?:provide|share|send|upload|attach|confirm)\b)",
    re.IGNORECASE,
)


def customer_visible_privacy_markers(statement: str) -> tuple[str, ...]:
    """Return categories that make prose unsafe for a customer reply.

    These are value-level last-line gates. Claims should describe customer
    behavior, never echo account identifiers, trace IDs, infrastructure names,
    or arbitrary email addresses observed during an investigation.
    """
    markers: list[str] = []
    for label, pattern in (
        ("email address", _EMAIL_RE),
        ("UUID", _UUID_RE),
        ("opaque internal identifier", _LONG_HEX_ID_RE),
        ("trace or request identifier", _SHORT_TRACE_ID_RE),
        ("tenant identifier", _TENANT_IDENTIFIER_RE),
        ("infrastructure workload name", _KUBERNETES_POD_RE),
        ("private network address", _PRIVATE_ADDRESS_RE),
        ("internal infrastructure hostname", _INTERNAL_HOST_RE),
        ("credential value", _CREDENTIAL_VALUE_RE),
        ("active markup", _ACTIVE_MARKUP_RE),
    ):
        if pattern.search(statement):
            markers.append(label)
    return tuple(markers)


def claim_is_declarative(statement: str) -> bool:
    """Claims are facts only; customer questions come from typed fields."""
    return "?" not in statement and not _CLAIM_REQUEST_RE.search(statement)


def claim_is_customer_safe(statement: str) -> bool:
    return bool(
        not _INTERNAL_DETAIL_RE.search(statement)
        and not customer_visible_privacy_markers(statement)
        and claim_is_declarative(statement)
    )
