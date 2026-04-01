"""Prompt 段定义与运行时上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PromptContext:
    """传递给动态 section 的运行时上下文。"""

    provider: str = "openai"
    model: str = ""
    ticket_type: str = ""
    strategy_content: str = ""
    customer_language: str = ""
    current_date: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptSection:
    """可条件加载的 prompt 段。"""

    name: str
    content: str | Callable[[PromptContext], str]
    order: int = 100
    condition: Callable[[PromptContext], bool] | None = None
    wrap_tag: str | None = None
