from __future__ import annotations

from dataclasses import dataclass

from zd_agent.runtime.llm.types import LLMResponse
from zd_agent.session.agent_session import AgentSession


class CaseworkTokenBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CaseworkTokenBudget:
    """One run-level model-token ledger with an optional legacy hard limit."""

    session: AgentSession
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.maximum is not None and self.maximum < 1:
            raise ValueError("casework token budget must be positive")

    def ensure_available(self) -> None:
        if (
            self.maximum is not None
            and self.session.token_usage.total_tokens >= self.maximum
        ):
            raise CaseworkTokenBudgetExceeded("casework model token budget exhausted")

    def record(self, response: LLMResponse) -> None:
        self.session.token_usage.prompt_tokens += response.prompt_tokens
        self.session.token_usage.completion_tokens += response.completion_tokens
        if (
            self.maximum is not None
            and self.session.token_usage.total_tokens > self.maximum
        ):
            raise CaseworkTokenBudgetExceeded("casework model token budget exceeded")
