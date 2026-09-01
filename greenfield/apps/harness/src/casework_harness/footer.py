"""Deterministic, localized customer-reply footers.

The model owns the answer or question body.  This module owns the footer so a
retry, a locale change, or an already-marked draft cannot make the customer
receive nested boilerplate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# This marker consists only of invisible Unicode characters.  It survives in
# the Zendesk text payload while remaining absent from the rendered footer.
# Keep it stable: changing it would make old marked drafts impossible to
# replace during an outbox retry.
CANONICAL_FOOTER_MARKER = "\u2063\u2060\u200b\u200c\u200d\u2063"


def _locale_key(locale: str | None) -> str:
    if not locale:
        return ""
    return locale.strip().replace("_", "-").casefold()


def _language_key(locale: str) -> str:
    return locale.split("-", 1)[0]


@dataclass(frozen=True, slots=True)
class FooterCatalog:
    """Versioned footer text indexed by BCP-47-like locale keys.

    A region-specific match wins.  A language-only match is accepted next,
    followed by the configured default locale.  The default must exist so an
    unsupported customer locale can never produce an empty footer.
    """

    default_locale: str
    footers: Mapping[str, str]

    def __post_init__(self) -> None:
        default = _locale_key(self.default_locale)
        if not default:
            raise ValueError("default_locale must not be empty")
        normalized: dict[str, str] = {}
        for locale, text in self.footers.items():
            key = _locale_key(locale)
            if not key:
                raise ValueError("footer locale must not be empty")
            if not text.strip():
                raise ValueError(f"footer for {locale!r} must not be empty")
            if key in normalized:
                raise ValueError(f"duplicate footer locale: {locale!r}")
            normalized[key] = text.strip()
        if default not in normalized and _language_key(default) not in normalized:
            raise ValueError("default_locale must have configured footer text")
        object.__setattr__(self, "default_locale", default)
        object.__setattr__(self, "footers", normalized)

    def resolve(self, locale: str | None) -> str:
        requested = _locale_key(locale)
        candidates = (
            requested,
            _language_key(requested) if requested else "",
            self.default_locale,
            _language_key(self.default_locale),
        )
        for candidate in candidates:
            if candidate and candidate in self.footers:
                return self.footers[candidate]
        # __post_init__ proves that one of the default candidates exists.
        raise RuntimeError("footer catalog default unexpectedly unavailable")


def _strip_existing_footer(body: str) -> str:
    """Remove a footer beginning at the canonical marker.

    The marker is deliberately treated as a suffix delimiter rather than
    trying to recognize translated prose.  Thus every old locale is replaced
    even if its text has since been edited in configuration.
    """

    marker_at = body.find(CANONICAL_FOOTER_MARKER)
    if marker_at < 0:
        return body.rstrip()
    return body[:marker_at].rstrip()


def append_localized_footer(
    body: str,
    *,
    locale: str | None,
    catalog: FooterCatalog,
) -> str:
    """Return ``body`` with exactly one footer for ``locale``.

    Existing marked footers are replaced, making the operation idempotent and
    safe to invoke both when creating and when retrying an outbox entry.
    """

    clean_body = _strip_existing_footer(body)
    footer = catalog.resolve(locale)
    if not clean_body:
        return f"{CANONICAL_FOOTER_MARKER}{footer}"
    return f"{clean_body}\n\n{CANONICAL_FOOTER_MARKER}{footer}"


# Friendly aliases for adapters that call this a renderer rather than an
# appender.  Keeping one implementation avoids subtly different idempotency
# behavior across delivery paths.
render_localized_footer = append_localized_footer
LocalizedFooterCatalog = FooterCatalog


__all__ = [
    "CANONICAL_FOOTER_MARKER",
    "FooterCatalog",
    "LocalizedFooterCatalog",
    "append_localized_footer",
    "render_localized_footer",
]
