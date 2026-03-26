"""上下文压缩：当 token 接近上限时压缩历史对话"""

from __future__ import annotations

from typing import Any

import tiktoken

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.llm_client import LLMClient

logger = get_logger(module="compaction")

# 保留最近 N 轮对话不被压缩
KEEP_RECENT_ROUNDS = 6


def count_tokens(messages: list[dict[str, Any]], model: str = "gpt-4o") -> int:
    """估算 messages 的 token 数"""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    total = 0
    for msg in messages:
        # 每条消息的元数据开销约 4 tokens
        total += 4
        if content := msg.get("content"):
            if isinstance(content, str):
                total += len(encoding.encode(content))
        if tool_calls := msg.get("tool_calls"):
            for tc in tool_calls:
                if fn := tc.get("function"):
                    total += len(encoding.encode(fn.get("name", "")))
                    total += len(encoding.encode(fn.get("arguments", "")))
    return total


async def compact_messages(
    messages: list[dict[str, Any]],
    llm: LLMClient,
) -> list[dict[str, Any]]:
    """压缩历史对话：保留 system prompt + 最近 N 轮 + 早期内容摘要

    返回压缩后的新 messages 列表。
    """
    if len(messages) <= KEEP_RECENT_ROUNDS + 2:
        return messages

    # system prompt 始终保留
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= KEEP_RECENT_ROUNDS:
        return messages

    # 拆分：早期对话 vs 最近对话
    # 关键约束：不能让 recent 以 tool 消息开头，否则会破坏
    # assistant(tool_calls) -> tool 的消息配对关系，触发 OpenAI 400。
    split_idx = _find_safe_split_index(non_system, KEEP_RECENT_ROUNDS)
    early = non_system[:split_idx]
    recent = non_system[split_idx:]

    # 将早期对话交给 LLM 做摘要
    early_text = _messages_to_text(early)
    if not early_text.strip():
        return messages

    logger.info(
        "compaction_start",
        early_messages=len(early),
        recent_messages=len(recent),
    )

    summary_prompt = [
        {
            "role": "system",
            "content": (
                "你是一个对话摘要助手。请将以下工单处理对话历史浓缩为简洁的摘要，"
                "保留关键信息：发现了什么问题、检查了哪些代码/配置、得出了什么结论。"
                "摘要控制在 500 字以内。"
            ),
        },
        {"role": "user", "content": early_text},
    ]

    try:
        resp = await llm.chat(summary_prompt)
        summary = resp.content or "（摘要生成失败）"
    except Exception as e:
        logger.warning("compaction_failed", error=str(e))
        summary = _messages_to_text(early)[:2000] + "\n\n... (早期对话已截断)"

    # 组装压缩后的 messages
    compacted = system_msgs + [
        {
            "role": "user",
            "content": f"[以下是之前分析过程的摘要]\n\n{summary}",
        },
    ] + recent

    before_tokens = count_tokens(messages)
    after_tokens = count_tokens(compacted)

    logger.info(
        "compaction_done",
        before_messages=len(messages),
        after_messages=len(compacted),
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        saved_tokens=before_tokens - after_tokens,
    )

    return compacted


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    """将 messages 转为可读文本（用于摘要输入）"""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "assistant" and msg.get("tool_calls"):
            tool_names = [
                tc.get("function", {}).get("name", "?")
                for tc in msg["tool_calls"]
            ]
            lines.append(f"[Assistant 调用工具: {', '.join(tool_names)}]")
        elif role == "tool":
            tool_name = msg.get("name", "unknown")
            # 截断过长的工具输出
            output = (content[:500] + "...") if len(content) > 500 else content
            lines.append(f"[工具 {tool_name} 返回]: {output}")
        elif content:
            lines.append(f"[{role}]: {content}")

    return "\n".join(lines)


def _find_safe_split_index(messages: list[dict[str, Any]], keep_recent: int) -> int:
    """计算压缩切点，避免 recent 以孤立的 tool 消息开头"""
    if len(messages) <= keep_recent:
        return 0

    start = max(0, len(messages) - keep_recent)

    # 如果切点刚好落在 tool 上，则向前回退，直到包含触发该 tool 的 assistant(tool_calls)
    while start > 0 and (
        messages[start].get("role") == "tool"
        or messages[start - 1].get("role") == "tool"
    ):
        start -= 1

    return start
