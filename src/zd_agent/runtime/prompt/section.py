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
    # SaaS followup / playbook integration
    mode: str = "first_pass"          # "first_pass" | "followup"
    previous_record: str = ""         # 历史工单记录内容（followup 模式注入）
    new_comments_diff: str = ""       # 与上次比新增的客户对话
    # P2-8 客户画像（dispatch 前预拉）
    customer_profile: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptSection:
    """可条件加载的 prompt 段。"""

    name: str
    content: str | Callable[[PromptContext], str]
    order: int = 100
    condition: Callable[[PromptContext], bool] | None = None
    wrap_tag: str | None = None
