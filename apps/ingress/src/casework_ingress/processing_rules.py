"""Pure, deterministic processing rules for normalized Zendesk events.

The ingress boundary deliberately has no queue or Zendesk dependencies.  It
only decides whether a normalized event should be enqueued, ignored, or used
to cancel an active run after a customer asks for a human.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

_ACTIVE_STATUSES = frozenset({"new", "open"})
_HUMAN_KEYWORD = "human"
_ProcessingAction = Literal["enqueue", "ignore", "cancel"]


def _normalise_text(value: object) -> str:
    """Return a comparison-safe representation of one configured string."""

    return str(value).strip().casefold()


def _normalise_human_text(value: object) -> str:
    """Normalize a whole-message human alias without enabling substring matches."""

    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    start = 0
    end = len(normalized)
    while start < end and unicodedata.category(normalized[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(normalized[end - 1]).startswith("P"):
        end -= 1
    return normalized[start:end].strip()


def _normalise_tags(tags: Iterable[object] | str | None) -> tuple[str, ...]:
    if isinstance(tags, str):
        tags = (tags,)
    if tags is None:
        return ()
    return tuple(
        normalised
        for normalised in (_normalise_text(tag) for tag in tags)
        if normalised
    )


@dataclass(frozen=True, slots=True)
class NormalizedZendeskEvent:
    """The event fields needed by processing rules.

    Webhook parsing is outside this slice.  This value is intentionally small:
    the complete webhook payload never reaches the rule evaluator or queue.
    """

    ticket_id: int
    tags: tuple[str, ...] = ()
    assignee: str | int | None = None
    assignee_id: str | int | None = None
    status: str = ""
    is_customer_message: bool = False
    message_text: str = ""

    def __post_init__(self) -> None:
        if type(self.ticket_id) is not int or self.ticket_id <= 0:
            raise ValueError("ticket_id must be a positive integer")
        object.__setattr__(self, "tags", _normalise_tags(self.tags))
        object.__setattr__(self, "status", _normalise_text(self.status))
        object.__setattr__(self, "message_text", str(self.message_text or ""))


@dataclass(frozen=True, slots=True)
class ProcessingRuleConfig:
    """Configured values used by the deterministic processing rules."""

    saas_tag: str | None
    saas_assignee: str | int | None
    statuses: tuple[str, ...] = ("new", "open")
    human_aliases: tuple[str, ...] = ()
    # ``human`` remains a fixed keyword; this field allows future configured
    # keywords without changing the ordinary ticket eligibility conditions.
    human_keywords: tuple[str, ...] = (_HUMAN_KEYWORD,)

    def __post_init__(self) -> None:
        if self.saas_tag is None or not _normalise_text(self.saas_tag):
            raise ValueError("saas_tag must not be empty")
        if self.saas_assignee is None or not _normalise_text(self.saas_assignee):
            raise ValueError("saas_assignee must not be empty")
        object.__setattr__(self, "saas_tag", _normalise_text(self.saas_tag))
        statuses = tuple(_normalise_text(status) for status in self.statuses)
        if not statuses or any(status not in _ACTIVE_STATUSES for status in statuses):
            raise ValueError("statuses must contain only new and open")
        object.__setattr__(self, "statuses", tuple(dict.fromkeys(statuses)))
        object.__setattr__(
            self, "human_aliases", _normalise_human_strings(self.human_aliases)
        )
        keywords = _normalise_human_strings(self.human_keywords)
        if _HUMAN_KEYWORD not in keywords:
            keywords = (_HUMAN_KEYWORD, *keywords)
        object.__setattr__(self, "human_keywords", keywords)


def _normalise_human_strings(
    values: Iterable[object] | str | None,
) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if values is None:
        return ()
    result: list[str] = []
    for value in values:
        normalized = _normalise_human_text(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ProcessingDecision:
    """Result of evaluating one normalized event.

    ``business_payload`` is populated only for an enqueue decision and is
    deliberately restricted to the ticket identifier required by the harness.
    """

    action: _ProcessingAction
    ticket_id: int
    reason: str
    _business_payload: dict[str, int] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def business_payload(self) -> dict[str, int] | None:
        if self._business_payload is None:
            return None
        # Return a fresh mapping so callers cannot mutate a frozen decision.
        return dict(self._business_payload)


def _decision(
    action: _ProcessingAction,
    event: NormalizedZendeskEvent,
    reason: str,
) -> ProcessingDecision:
    payload = {"ticket_id": event.ticket_id} if action == "enqueue" else None
    return ProcessingDecision(action, event.ticket_id, reason, payload)


def _has_human_request(
    event: NormalizedZendeskEvent, config: ProcessingRuleConfig
) -> bool:
    text = _normalise_human_text(event.message_text)
    if not text:
        return False
    triggers = (*config.human_keywords, *config.human_aliases)
    return text in triggers


def _has_individual_assignee(assignee_id: str | int | None) -> bool:
    if assignee_id is None:
        return False
    if isinstance(assignee_id, int):
        return assignee_id > 0
    if isinstance(assignee_id, str):
        cleaned = assignee_id.strip()
        return bool(cleaned and cleaned != "0" and cleaned.casefold() not in {"null", "none"})
    return False


def evaluate_processing_rules(
    event: NormalizedZendeskEvent,
    config: ProcessingRuleConfig,
) -> ProcessingDecision:
    """Evaluate one event without performing I/O.

    A human request is checked after confirming the event is a customer
    message, but before all ordinary SaaS eligibility gates.  Thus a customer
    human request can cancel an active run even when a ticket has just changed
    status, assignee, or tags, while an internal agent message cannot cancel a
    run merely by containing the word ``human``.
    """

    if not event.is_customer_message:
        text = (event.message_text or "").lower()
        mention_triggers = (
            "@david yang",
            "@davidyang",
            "**david yang**",
            "@agent",
            "@ai agent",
            "@ai",
            "@dify-agent",
            "@saas-agent",
        )
        if any(tr in text for tr in mention_triggers):
            return _decision("enqueue", event, "operator_mention")
        return _decision("ignore", event, "not_customer_message")

    if _has_human_request(event, config):
        return _decision("cancel", event, "human")

    if _has_individual_assignee(event.assignee_id):
        return _decision("ignore", event, "already_assigned_to_human")

    if config.saas_tag not in event.tags:
        return _decision("ignore", event, "saas_tag_missing")

    if _normalise_text(event.assignee) != _normalise_text(config.saas_assignee):
        return _decision("ignore", event, "saas_assignee_mismatch")

    if event.status not in config.statuses:
        return _decision("ignore", event, "status_out_of_scope")

    return _decision("enqueue", event, "eligible")
