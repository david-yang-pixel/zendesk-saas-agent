"""Agent 循环事件类型（精简版，仅服务于日志和 Subagent 复用）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentEvent:
    """事件基类"""
    round: int = 0


@dataclass
class LoopProgress(AgentEvent):
    """每轮进度（合并了 RoundStart + LLMResponse 的关键信息）"""
    message_count: int = 0
    token_count: int = 0
    llm_elapsed: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_call_count: int = 0


@dataclass
class ToolExecuted(AgentEvent):
    """工具执行完成"""
    tool_name: str = ""
    success: bool = True
    output_length: int = 0
    elapsed: float = 0.0
    error: str | None = None


@dataclass
class LoopFinished(AgentEvent):
    """循环结束"""
    status: str = "completed"  # "completed" | "guardrail" | "error"
    total_rounds: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    reason: str = ""
