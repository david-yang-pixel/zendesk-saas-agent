from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CompactionLevel(StrEnum):
    NONE = "none"
    SOFT = "soft"
    HARD = "hard"


@dataclass(frozen=True, slots=True)
class ContextBudget:
    window_tokens: int
    soft_ratio: float = 0.60
    hard_ratio: float = 0.80

    def __post_init__(self) -> None:
        if self.window_tokens <= 0:
            raise ValueError("context window must be positive")
        if not 0 < self.soft_ratio < self.hard_ratio <= 1:
            raise ValueError("compaction ratios must satisfy 0 < soft < hard <= 1")

    def level_for(self, used_tokens: int) -> CompactionLevel:
        if used_tokens < 0:
            raise ValueError("context usage cannot be negative")
        if used_tokens > self.window_tokens:
            raise ValueError("context usage exceeds context window")
        ratio = used_tokens / self.window_tokens
        if ratio >= self.hard_ratio:
            return CompactionLevel.HARD
        if ratio >= self.soft_ratio:
            return CompactionLevel.SOFT
        return CompactionLevel.NONE
