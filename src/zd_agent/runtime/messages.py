"""消息存储：以统一格式管理对话历史，按需转换厂商格式。"""

from __future__ import annotations

from typing import Any

from zd_agent.runtime.llm.types import LLMProvider


class MessageStore:
    """厂商无关的消息存储。

    内部以 OpenAI 兼容格式存储（role/content/tool_calls），
    通过 provider.format_tool_result 处理厂商差异。
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._messages: list[dict[str, Any]] = []
        self._provider = provider

    def append_system(self, content: str) -> None:
        self._messages.append({"role": "system", "content": content})

    def append_user(self, content: str | list[dict[str, Any]]) -> None:
        self._messages.append({"role": "user", "content": content})

    def append_assistant(self, raw_message: Any) -> None:
        """追加 assistant 消息（接受 provider 返回的 raw 或 dict）"""
        if isinstance(raw_message, dict):
            self._messages.append(raw_message)
        else:
            self._messages.append({"role": "assistant", "content": str(raw_message)})

    def append_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        msg = self._provider.format_tool_result(tool_call_id, name, content)
        self._messages.append(msg)

    def to_api_messages(self) -> list[dict[str, Any]]:
        """返回消息列表（当前直接返回内部列表的引用）"""
        return self._messages

    @property
    def raw(self) -> list[dict[str, Any]]:
        return self._messages

    @raw.setter
    def raw(self, value: list[dict[str, Any]]) -> None:
        self._messages = value

    def __len__(self) -> int:
        return len(self._messages)
