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


# 粗估 token ≈ chars / 4（英文 / 结构化数据偏保守，中文实际比例更高）。
# 工具输出多是结构化 JSON / log 行，4 倍率合理。
_CHARS_PER_TOKEN = 4


def _enforce_max_output(
    result: ToolResult, max_tokens: int, tool_name: str,
) -> ToolResult:
    """强制截断 tool 返回，超过 max_tokens*4 字符就截到 90% + 截断标记。

    max_tokens=0 → 不截断（默认行为，向后兼容）。
    截断只动 output 字段，error/success 不动。
    """
    if max_tokens <= 0 or not result.output:
        return result
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(result.output) <= max_chars:
        return result
    keep = int(max_chars * 0.9)
    truncated_output = (
        result.output[:keep]
        + f"\n\n[... {tool_name} output truncated from {len(result.output)} "
          f"to {keep} chars (max_output_tokens={max_tokens}). "
          f"Add filters / GROUP BY / LIMIT to narrow your query, "
          f"or call again with a more specific argument.]"
    )
    return ToolResult(
        success=result.success,
        output=truncated_output,
        error=result.error,
    )


# 数据查询类工具失败时，给 LLM 的错误文本加一条防臆造横幅。
# 根因：工具失败 = 没拿到数据，但 quality bar 又逼 agent 给"具体诊断+证据"，
# 模型会用幻觉填空（#2709：inspector 超时 → 编了个不存在的 credential error）。
# 在工具层把"别编"硬注入到每条数据工具失败信息里，不依赖模型记得 SOUL.md。
_DATA_QUERY_GROUP = "database_query"
_NO_FABRICATION_BANNER = (
    "\n\n⚠️ 数据未获取（工具失败/超时）。严禁基于猜测编造查询结果或 error 内容。"
    "下一步：换工具/改条件重查；仍拿不到 → 标 needs_human=True 并诚实写"
    "'未获取到数据'，不要给客户一个臆造的根因。"
)


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
        """根据工具名执行工具，args_json 是 LLM 返回的 JSON 字符串。

        ⚠️ enforce ``tool.max_output_tokens``：之前各 tool 声明的 max_output_tokens
        是装饰未生效，导致 SigNoz 一次返回 146KB / 35K tokens 塞回 agent，
        message_store 暴涨 → token guardrail 触发 forced_summary。

        现在在 ToolRegistry 这层强制截断：result.output 字符数超过
        ``max_output_tokens × 4``（粗估 1 token ≈ 4 chars）→ 保留前 90%，
        加截断提示让 agent 改 query 重查。max_output_tokens=0 不截。
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"未知工具: {tool_name}")

        try:
            args = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"参数解析失败: {e}")

        try:
            result = await tool.execute(args, session)
        except Exception as e:
            result = ToolResult(success=False, error=f"工具执行异常: {e}")

        # 数据查询类工具失败 → 给 error 文本附防臆造横幅（堵 #2709 类幻觉）
        if (
            not result.success
            and tool.group == _DATA_QUERY_GROUP
            and result.error
            and "数据未获取" not in result.error  # inspector 已自带更详细提示则不重复
        ):
            result = ToolResult(
                success=result.success,
                output=result.output,
                error=result.error + _NO_FABRICATION_BANNER,
            )

        # Enforce max_output_tokens
        return _enforce_max_output(result, tool.max_output_tokens, tool_name)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
