"""统一重试助手：支持全局默认值 + 模块级覆盖 + 指数退避"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from zd_agent.config import get_config
from zd_agent.log import get_logger

logger = get_logger(module="retry")

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """解析后的重试策略"""

    name: str
    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    jitter: bool


def get_retry_policy(name: str) -> RetryPolicy:
    """读取指定模块的重试策略；未覆盖时继承全局默认值"""
    cfg = get_config().retry
    override = getattr(cfg, name)
    return RetryPolicy(
        name=name,
        max_attempts=override.max_attempts or cfg.max_attempts,
        base_delay_seconds=override.base_delay_seconds or cfg.base_delay_seconds,
        max_delay_seconds=override.max_delay_seconds or cfg.max_delay_seconds,
        jitter=cfg.jitter,
    )


def compute_delay(
    policy: RetryPolicy, attempt: int, suggested_delay: float | None = None
) -> float:
    """计算当前重试等待时间，支持外部建议值（如 Retry-After）"""
    delay = suggested_delay or (policy.base_delay_seconds * (2 ** (attempt - 1)))
    delay = min(delay, policy.max_delay_seconds)
    if policy.jitter:
        delay *= random.uniform(0.8, 1.2)
    return max(0.0, delay)


async def retry_async(
    *,
    operation: str,
    policy: RetryPolicy,
    func: Callable[[], Awaitable[T]],
    should_retry: Callable[[Exception], bool],
    get_delay: Callable[[Exception, int, RetryPolicy], float | None] | None = None,
    log_context: dict[str, Any] | None = None,
) -> T:
    """按给定策略重试异步操作"""
    last_error: Exception | None = None
    context = log_context or {}

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await func()
        except Exception as e:
            last_error = e
            retryable = should_retry(e)
            exhausted = attempt >= policy.max_attempts
            if (not retryable) or exhausted:
                if retryable and exhausted:
                    logger.error(
                        "retries_exhausted",
                        operation=operation,
                        policy=policy.name,
                        attempts=policy.max_attempts,
                        error_type=type(e).__name__,
                        error=str(e),
                        **context,
                    )
                raise

            suggested_delay = get_delay(e, attempt, policy) if get_delay else None
            delay = compute_delay(policy, attempt, suggested_delay)
            logger.warning(
                "retry_scheduled",
                operation=operation,
                policy=policy.name,
                attempt=attempt,
                max_attempts=policy.max_attempts,
                retry_in=f"{delay:.1f}s",
                error_type=type(e).__name__,
                error=str(e),
                **context,
            )
            await asyncio.sleep(delay)

    raise last_error or RuntimeError("unexpected retry state")
