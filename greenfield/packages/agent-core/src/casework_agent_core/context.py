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

    def __init__(
        self,
        *,
        window_tokens: int,
        retain_rounds: int = 4,
        soft_compaction_percent: int = 60,
        hard_checkpoint_percent: int = 80,
        summary_char_limit: int = 4096,
    ) -> None:
        self._budget = ContextBudget(
            window_tokens=window_tokens,
            soft_ratio=soft_compaction_percent / 100,
            hard_ratio=hard_checkpoint_percent / 100,
        )
        if retain_rounds < 1 or summary_char_limit < 1:
            raise ValueError("context retention limits must be positive")
        self._retain_rounds = retain_rounds
        self._summary_char_limit = summary_char_limit
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
            summary = self._bounded_summary(texts[:-self._retain_rounds])
        elif level is CompactionLevel.SOFT:
            retained = tuple(texts[-self._retain_rounds :])
            summary = self._bounded_summary(texts[:-self._retain_rounds])
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

    def compact_if_needed(
        self, *, active_question: ActiveQuestion
    ) -> ContextCheckpoint:
        """Checkpoint and rebuild immediately when a configured threshold fires."""

        checkpoint = self.checkpoint(active_question=active_question)
        if checkpoint.level is CompactionLevel.NONE:
            return checkpoint
        return self.rebuild(checkpoint)

    def rebuild(self, checkpoint: ContextCheckpoint) -> ContextCheckpoint:
        """Rebuild a bounded context from a validated checkpoint.

        Raw tool payloads are intentionally absent from a checkpoint.  The
        retained rounds and summary are the only context reintroduced; exact
        identifiers and the active question are carried separately.
        """

        self._items = []
        self._used_tokens = 0
        if checkpoint.summary:
            summary_tokens = max(1, len(checkpoint.summary) // 4)
            if summary_tokens <= self._budget.window_tokens:
                self._items.append((checkpoint.summary, summary_tokens))
                self._used_tokens = summary_tokens
        for text in checkpoint.retained_rounds:
            tokens = max(1, len(text) // 4)
            if self._used_tokens + tokens > self._budget.window_tokens:
                break
            self._items.append((text, tokens))
            self._used_tokens += tokens
        return self.checkpoint(active_question=checkpoint.active_question)

    def _bounded_summary(self, values: list[str]) -> str:
        summary = " ".join(values)
        if len(summary) <= self._summary_char_limit:
            return summary
        return summary[: self._summary_char_limit].rstrip()

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
        for event_id in (
            active_question.original_event_id,
            *active_question.clarification_event_ids,
        ):
            if event_id not in found:
                found.append(event_id)
        return tuple(found)
