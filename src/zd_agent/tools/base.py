"""工具基类与注册中心"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


class ToolResult(BaseModel):
    """工具执行结果"""

    success: bool = True
    output: str = ""
    error: str | None = None


class Tool(ABC):
    """工具抽象基类，所有工具继承此类"""

    name: str
    description: str
    parameters: dict[str, Any]

    # 元数据（默认值向后兼容，现有子类无需改动）
    concurrency_safe: bool = True
    group: str = "default"
    max_output_tokens: int = 0
    requires_sandbox: bool = False
    is_write_operation: bool = False

    @abstractmethod
    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        ...

    def to_openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册中心：管理所有可用工具，提供统一的注册、查找和执行接口"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具名冲突: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """转换为 OpenAI API 所需的 tools 参数格式"""
        return [t.to_openai_schema() for t in self._tools.values()]

    async def execute(
        self, tool_name: str, args_json: str, session: AgentSession
    ) -> ToolResult:
        """根据工具名执行工具，args_json 是 LLM 返回的 JSON 字符串"""
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"未知工具: {tool_name}")

        try:
            args = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"参数解析失败: {e}")

        try:
            return await tool.execute(args, session)
        except Exception as e:
            return ToolResult(success=False, error=f"工具执行异常: {e}")

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
