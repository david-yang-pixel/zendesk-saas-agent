"""护栏模块：轮次、超时、token 预算控制"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from zd_agent.config import GuardrailsConfig
from zd_agent.log import get_logger

logger = get_logger(module="guardrails")


class GuardrailTriggered(Exception):
    """护栏触发时抛出的异常"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class Guardrails:
    """运行时护栏：检查轮次、超时、token 预算"""

    config: GuardrailsConfig
    start_time: float = field(default_factory=time.monotonic)
    current_round: int = 0
    total_tokens: int = 0

    def check_round(self) -> None:
        """检查是否超过最大轮次"""
        if self.current_round >= self.config.max_rounds:
            raise GuardrailTriggered(
                f"达到最大轮次限制: {self.current_round}/{self.config.max_rounds}"
            )

    def check_timeout(self) -> None:
        """检查是否超时"""
        elapsed = time.monotonic() - self.start_time
        if elapsed > self.config.timeout_seconds:
            raise GuardrailTriggered(
                f"处理超时: {elapsed:.0f}s > {self.config.timeout_seconds}s"
            )

    def check_tokens(self) -> None:
        """检查 token 是否超预算"""
        if self.total_tokens > self.config.max_tokens_per_session:
            raise GuardrailTriggered(
                f"Token 超预算: {self.total_tokens} > {self.config.max_tokens_per_session}"
            )

    def check_all(self) -> None:
        """执行所有护栏检查"""
        self.check_round()
        self.check_timeout()
        self.check_tokens()

    def should_compact(self, message_store_tokens: int = 0) -> bool:
        """判断是否需要触发 compaction，二选一触发：

        1. cumulative ``total_tokens > max_tokens_per_session * compaction_threshold``
           （传统：累计触发，控制总成本）
        2. ``message_store_tokens > 80_000``（**修法 2 新增**：每轮 message_store
           太大就压缩，控制单次 LLM input 大小，避免 forced_summary on token）

        message_store_tokens=0（不传）时只检查累计条件，向后兼容。
        """
        # 累计条件（原逻辑）
        cum_threshold = int(
            self.config.max_tokens_per_session * self.config.compaction_threshold,
        )
        if self.total_tokens > cum_threshold:
            return True
        # message_store 大小条件（新增，更主动）
        # 80K tokens ≈ 单次 LLM call input 上限，超过就有 forced_summary 风险
        if message_store_tokens > 80_000:
            return True
        return False

    def increment_round(self) -> None:
        self.current_round += 1

    def add_tokens(self, prompt: int, completion: int) -> None:
        self.total_tokens += prompt + completion

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    def is_approaching_limit(self) -> bool:
        """检查是否接近轮次限制（用于日志警告）"""
        return self.current_round >= self.config.max_rounds - 5
