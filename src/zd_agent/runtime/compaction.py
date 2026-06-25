"""上下文压缩：当 token 接近上限时压缩历史对话"""

from __future__ import annotations

from typing import Any

import tiktoken

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.llm.types import LLMProvider
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
    llm: LLMClient | LLMProvider,
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


# --- 锚点与多级压缩（在现有 compact_messages 之上叠加）---

# 锚点工具：其 tool 消息全文保留，不参与 History Snip 裁剪
ANCHOR_TOOL_NAMES = {
    "zendesk_get_ticket",
    "classify_ticket",
    "zendesk_internal_reply",
}
SUBAGENT_PARALLEL_TOOL_NAME = "run_subagents_parallel"
SUBAGENT_PARALLEL_SNIP_CHARS_PER_BLOCK = 600


def is_anchor_message(msg: dict[str, Any]) -> bool:
    """判断消息是否为锚点（不可被 Level 1 压缩裁剪）。"""
    if msg.get("role") == "system":
        return True
    if msg.get("role") == "tool" and msg.get("name") in ANCHOR_TOOL_NAMES:
        return True
    return False


def _split_subagent_parallel_blocks(content: str) -> list[str]:
    lines = content.splitlines(keepends=True)
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("=== Subagent ") and current:
            blocks.append("".join(current).strip())
            current = [line]
            continue
        current.append(line)
    if current:
        blocks.append("".join(current).strip())
    if len(blocks) > 1:
        return blocks
    fallback_blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
    return fallback_blocks or [content]


def _snip_subagent_parallel_output(content: str) -> str:
    blocks = _split_subagent_parallel_blocks(content)
    snipped_blocks: list[str] = []
    for block in blocks:
        if len(block) <= SUBAGENT_PARALLEL_SNIP_CHARS_PER_BLOCK:
            snipped_blocks.append(block)
            continue
        snipped_blocks.append(
            block[:SUBAGENT_PARALLEL_SNIP_CHARS_PER_BLOCK]
            + "\n\n[... 此子 Agent 输出已精简 ...]"
        )
    return "\n\n".join(snipped_blocks)


def history_snip(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Level 1 压缩：缩减非锚点工具结果的内容长度。

    对非锚点 tool 消息，将 content 裁剪为前 200 字符 + 截断提示。
    不消耗 LLM 调用。
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if is_anchor_message(msg):
            result.append(msg)
            continue

        if msg.get("role") == "tool" and msg.get("name") not in ANCHOR_TOOL_NAMES:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 300:
                snipped = dict(msg)
                if msg.get("name") == SUBAGENT_PARALLEL_TOOL_NAME:
                    snipped["content"] = _snip_subagent_parallel_output(content)
                    result.append(snipped)
                    continue
                snipped["content"] = content[:200] + "\n\n[... 输出已精简 ...]"
                result.append(snipped)
                continue

        result.append(msg)
    return result


async def multi_level_compact(
    messages: list[dict[str, Any]],
    llm_or_provider: LLMClient | LLMProvider,
    target_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """多级压缩调度器：依次尝试 Level 1 → Level 2。

    target_tokens 为 None 时使用配置的 compaction_threshold 推算。
    """
    if target_tokens is None:
        cfg = get_config()
        max_tokens = cfg.agent.guardrails.max_tokens_per_session
        target_tokens = int(max_tokens * cfg.agent.guardrails.compaction_threshold)

    current = messages
    current_tokens = count_tokens(current)

    if current_tokens <= target_tokens:
        return current

    # Level 1：History Snip（不消耗 LLM 调用）
    current = history_snip(current)
    current_tokens = count_tokens(current)
    logger.info("compaction_level1_done", tokens_after=current_tokens)
    if current_tokens <= target_tokens:
        return current

    # Level 2：LLM 摘要（沿用现有 compact_messages）
    current = await compact_messages(current, llm_or_provider)
    logger.info("compaction_level2_done", tokens_after=count_tokens(current))
    return current
