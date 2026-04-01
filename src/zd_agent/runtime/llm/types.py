"""厂商无关的 LLM 抽象类型与接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCallRequest:
    """单次工具调用请求（arguments 为 JSON 字符串，与 registry.execute 入参一致）。"""

    id: str
    name: str
    arguments: str


@dataclass
class ToolSchema:
    """工具的 JSON Schema 描述。"""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class LLMResponse:
    """单次 chat 补全的归一化结果。"""

    content: str | None
    tool_calls: list[ToolCallRequest]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    raw: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """各厂商 LLM 客户端需实现的接口。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """发送 chat 请求，可选注册工具。"""

    @abstractmethod
    def format_tool_result(
        self, tool_call_id: str, name: str, content: str
    ) -> dict[str, Any]:
        """将工具执行结果转为该厂商 messages 中的单条结构。"""
