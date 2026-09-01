"""Context usage, checkpointing and lossless identifier retention."""

from __future__ import annotations

import re
from dataclasses import dataclass

from casework_agent_core.context_budget import CompactionLevel, ContextBudget
from casework_agent_core.domain import ActiveQuestion


@dataclass(frozen=True, slots=True)
class ContextCheckpoint:
    level: CompactionLevel
    used_tokens: int
    active_question: ActiveQuestion
    exact_identifiers: tuple[str, ...]
    retained_rounds: tuple[str, ...]
    summary: str


class ContextGovernance:
    """A small in-memory checkpoint manager used by one sandbox run."""

    def __init__(self, *, window_tokens: int, retain_rounds: int = 4) -> None:
        self._budget = ContextBudget(window_tokens=window_tokens)
        self._retain_rounds = retain_rounds
        self._items: list[tuple[str, int]] = []
        self._used_tokens = 0

    def add(self, text: str, *, tokens: int) -> None:
        if tokens < 1:
            raise ValueError("context item tokens must be positive")
        if self._used_tokens + tokens > self._budget.window_tokens:
            raise ValueError("context usage exceeds context window")
        self._items.append((text, tokens))
        self._used_tokens += tokens

    def checkpoint(self, *, active_question: ActiveQuestion) -> ContextCheckpoint:
        level = self._budget.level_for(self._used_tokens)
        texts = [item[0] for item in self._items]
        identifiers = self._extract_identifiers(texts, active_question)
        if level is CompactionLevel.HARD:
            retained = tuple(texts[-self._retain_rounds :])
            summary = " ".join(texts)
        elif level is CompactionLevel.SOFT:
            retained = tuple(texts[-self._retain_rounds :])
            summary = " ".join(texts[:-self._retain_rounds])
        else:
            retained = tuple(texts)
            summary = ""
        return ContextCheckpoint(
            level=level,
            used_tokens=self._used_tokens,
            active_question=active_question,
            exact_identifiers=identifiers,
            retained_rounds=retained,
            summary=summary,
        )

    @staticmethod
    def _extract_identifiers(
        texts: list[str], active_question: ActiveQuestion
    ) -> tuple[str, ...]:
        found: list[str] = []
        for text in texts:
            for match in re.findall(r"(?:ticket|evt(?:ent)?)[-_ ]?\d+", text, re.I):
                normalized = match.replace("ticket ", "").replace("event ", "")
                normalized = normalized.replace("evt-", "evt-")
                if normalized not in found:
                    found.append(normalized)
        ticket_ids = re.findall(r"\d+", " ".join(texts))
        if ticket_ids:
            ticket_id = ticket_ids[0]
            if ticket_id not in found:
                found.insert(0, ticket_id)
        for event_id in active_question.event_ids:
            if event_id not in found:
                found.append(event_id)
        return tuple(found)
