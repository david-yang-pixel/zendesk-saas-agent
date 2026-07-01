"""Public reply 模板生成（仅 SaaS messaging 工单，统一英文）。

设计决策（来自 2026-05-13 跟用户讨论）：
- **统一 English**：messaging 工单一律英文回复，不跟随客户语言
- **不问已知字段**：Workspace ID / customer name / email 在 formResponse 里已有，
  绝不向客户索取（agent 已经从 ``_form_data_from_msgs`` 拿到）
- **footer 不在这里加**：``ZendeskPublicReply`` 工具层硬编码追加，本模块只输出
  正文 body（不含 footer 不含 ``---`` 分隔线）
- **追问 LLM-first**：A/B/C/D 表示场景语义和 fallback；正常路径由 LLM 生成
  最小客户追问，失败或命中安全过滤时回退到对应模板

7 个模板对应 6 种触发场景 + 1 个 agent 给方案的 skeleton：
- A silent          客户工单 0 消息（含 formResponse 已抽 name/wid）
- B greeting        客户只发 hi / hello / 在吗
- C insufficient    客户说了但太模糊 → 要求补关键排查信息
- D retry           第 2/3+ 次还没说清，措辞更直接 + 给具体例子
- E handoff         转人工通知
- F solution        agent 给方案（agent 自己组装正文，这里仅提供 hint）
- G closed          客户说已解决，温和收尾
"""
from __future__ import annotations

import json
from typing import Literal

import httpx

from zd_agent.log import get_logger
from zd_agent.prompts.public_reply.ask_body_generation import (
    MISSING_INFO_FALLBACK_BULLETS,
    generate_ask_body_with_llm as _generate_ask_body_with_llm,
)

logger = get_logger(module="prompts.public_reply")

AskTemplateName = Literal["A", "B", "C", "D"]


# ---------------------------------------------------------------------------
# 静态模板（A / B / D / E / G）
# ---------------------------------------------------------------------------

def silent_template(*, customer_name: str = "") -> str:
    """A. Customer created ticket but hasn't sent any messages yet.

    触发：trigger_decider verdict=no_message（仅 formResponse）。
    """
    name_part = f" {customer_name}" if customer_name else ""
    return (
        f"Hi{name_part}! We've received your ticket. Could you briefly describe the issue?\n\n"
        "- Which feature or page is affected?\n"
        "- The error message or a screenshot (if any)\n"
        "- What you were trying to do when it happened"
    )


def greeting_template(*, customer_name: str = "") -> str:
    """B. Customer just said hi (no problem stated).

    触发：trigger_decider verdict=greeting。
    """
    name_part = f" {customer_name}" if customer_name else ""
    return (
        f"Hi{name_part}! What can I help you with? "
        "If possible, please share the error you're seeing, a screenshot, or steps to reproduce."
    )


def retry_template(*, customer_name: str = "") -> str:
    """D. 2nd / 3rd+ ask — customer still hasn't given enough info.

    触发：第 2 次及以后 ask（ask_count >= 1）+ verdict ∈ {greeting, insufficient_info}。
    措辞比 B/C 更直接，给具体例子帮客户表达。
    """
    name_part = f" {customer_name}" if customer_name else ""
    return (
        f"Hi{name_part} — sorry, I still can't investigate without specifics. "
        "Could you describe **exactly what's happening**, for example:\n\n"
        '- "I get a 500 error when creating a workflow"\n'
        '- "My app page won\'t load on Chrome"\n'
        '- "My AI Credits show 0 but I only sent a few messages"'
    )


def handoff_template(*, customer_name: str = "") -> str:
    """E. Agent giving up, notifying customer that a human is taking over.

    触发：state→escalated_to_human（任何原因，包括 customer_requested /
    max_asks_reached / high_risk_category / low_confidence / agent_error）。
    """
    name_part = f" {customer_name}" if customer_name else ""
    return (
        f"Hi{name_part}, I'll let a human teammate take this from here. "
        "They'll follow up shortly. Thanks for your patience!"
    )


def closed_template(*, customer_name: str = "") -> str:
    """G. Customer said they resolved it (self_resolved verdict).

    触发：trigger_decider verdict=self_resolved。温和收尾 + 重启入口。
    """
    name_part = f" {customer_name}" if customer_name else ""
    return (
        f"Glad to hear that{name_part}! "
        "Closing this ticket — feel free to reply here or open a new ticket anytime."
    )


# ---------------------------------------------------------------------------
# 动态模板（C insufficient_info — 需要 LLM 生成"还缺啥"）
# ---------------------------------------------------------------------------

_MISSING_INFO_PROMPT = """Customer's recent messages on a Zendesk SaaS ticket:

<customer_messages>
{messages}
</customer_messages>

The customer described their problem but the description is too vague to debug. \
Generate 2-3 specific, actionable bullet points asking for **missing information**. \
Each point should ask for ONE specific thing.

Constraints:
- **DO NOT** ask for Workspace ID, customer name, or email — we already have these from the form
- Be specific to what's vague in their messages (avoid generic "more info please")
- Each bullet must start with "- " on its own line
- Use natural English, polite but direct
- Limit to 2-3 bullets total

Good examples:
- The specific app ID or workflow ID that's affected
- The exact error message text (or a screenshot)
- When this started happening / how often it reproduces

Bad examples (don't write):
- Your Workspace ID  ← we have it
- Your email  ← we have it
- More information about the issue  ← too vague

Return JSON: {{"bullets": ["- ...", "- ...", "- ..."]}}"""


# LLM 即使被 prompt 明确禁止，偶尔仍会生成"请提供 Workspace ID / 邮箱 / 姓名"
# 之类的 bullet —— 这三项 formResponse 已经有了，再问会让客户觉得冗余 + 不专业。
# 用 substring match (lowercased) 过滤掉，没什么误伤面：
#   - "workspace id" / "wid"        → 已知字段
#   - "your email" / "email address"→ 已知字段
#   - "your name" / "full name"     → 已知字段
_FORBIDDEN_BULLET_PHRASES = (
    "workspace id",
    "your workspace",
    "wid:",
    "your email",
    "email address",
    "your name",
    "full name",
    "your account name",
)


def _is_forbidden_bullet(bullet: str) -> bool:
    """判断 bullet 是否要求已知字段（应被过滤）。"""
    low = bullet.lower()
    return any(phrase in low for phrase in _FORBIDDEN_BULLET_PHRASES)


def _ask_template_name(*, verdict_kind: str, ask_count: int) -> AskTemplateName:
    if ask_count >= 1:
        return "D"
    match verdict_kind:
        case "no_message":
            return "A"
        case "greeting":
            return "B"
        case _:
            return "C"


def _insufficient_info_fallback_template(*, customer_name: str = "") -> str:
    name_part = f"{customer_name}, " if customer_name else ""
    bullet_text = "\n".join(MISSING_INFO_FALLBACK_BULLETS)
    return (
        f"{name_part}got it. To investigate faster, could you share:\n\n"
        f"{bullet_text}"
    )


def _fallback_ask_body(
    *,
    verdict_kind: str,
    ask_count: int,
    customer_name: str,
) -> tuple[str, AskTemplateName]:
    template_name = _ask_template_name(
        verdict_kind=verdict_kind,
        ask_count=ask_count,
    )
    match template_name:
        case "A":
            return silent_template(customer_name=customer_name), template_name
        case "B":
            return greeting_template(customer_name=customer_name), template_name
        case "D":
            return retry_template(customer_name=customer_name), template_name
        case "C":
            return (
                _insufficient_info_fallback_template(customer_name=customer_name),
                template_name,
            )
    raise ValueError(f"unsupported ask template: {template_name}")


async def _generate_missing_info_bullets(
    *,
    customer_messages: list[str],
    model: str,
    api_key: str,
    timeout: float = 10.0,
) -> list[str]:
    """Cheap LLM 生成 2-3 个 bullet 点，问客户缺啥具体信息。

    出错时 fallback 到通用的硬编码 3 点（仍能继续 ask 流程）。
    """
    # 只看最近 5 条客户消息，足够 LLM 判断且省 token
    recent = customer_messages[-5:] if customer_messages else []
    if not recent:
        return list(MISSING_INFO_FALLBACK_BULLETS)

    joined = "\n---\n".join(m for m in recent if m)
    prompt = _MISSING_INFO_PROMPT.format(messages=joined)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 200,
                    "response_format": {"type": "json_object"},
                },
            )
    except Exception as e:
        logger.warning("template_c_llm_http_error", error=str(e)[:200])
        return list(MISSING_INFO_FALLBACK_BULLETS)

    if resp.status_code != 200:
        logger.warning(
            "template_c_llm_bad_status",
            status=resp.status_code, body=resp.text[:200],
        )
        return list(MISSING_INFO_FALLBACK_BULLETS)

    try:
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        bullets = parsed.get("bullets") or []
        # 防御：只接受字符串列表 + 至少 1 个 bullet
        bullets = [
            str(b).strip() for b in bullets
            if isinstance(b, str) and b.strip()
        ][:3]
        if not bullets:
            return list(MISSING_INFO_FALLBACK_BULLETS)

        # 防御：每个 bullet 必须以 "- " 开头（M6 测试覆盖 LLM 可能漏了）
        normalized = []
        for b in bullets:
            if not b.startswith("- "):
                b = "- " + b.lstrip("-").lstrip()
            normalized.append(b)

        # post-filter：去掉问已知字段的 bullet（Workspace ID / email / name）
        # LLM 偶尔不听 prompt 约束，把 formResponse 已有的字段又写进 bullet
        # → SE 看到必觉得不专业。filter 后不足 2 条直接 fallback。
        filtered = [b for b in normalized if not _is_forbidden_bullet(b)]
        if len(filtered) < 2:
            dropped = [b for b in normalized if _is_forbidden_bullet(b)]
            logger.warning(
                "template_c_llm_filtered_too_many",
                kept=len(filtered), dropped=dropped,
            )
            return list(MISSING_INFO_FALLBACK_BULLETS)
        return filtered
    except Exception as e:
        logger.warning("template_c_llm_parse_error", error=str(e)[:200])
        return list(MISSING_INFO_FALLBACK_BULLETS)


async def insufficient_info_template(
    *,
    customer_name: str = "",
    customer_messages: list[str],
    model: str,
    api_key: str,
) -> str:
    """C. Customer described problem but too vaguely. Dynamic LLM fill.

    触发：trigger_decider verdict=insufficient_info, ask_count == 0
    （第 2 次及以后用 ``retry_template``）。
    """
    bullets = await _generate_missing_info_bullets(
        customer_messages=customer_messages, model=model, api_key=api_key,
    )
    name_part = f"{customer_name}, " if customer_name else ""
    bullet_text = "\n".join(bullets)
    return (
        f"{name_part}got it. To investigate faster, could you share:\n\n"
        f"{bullet_text}"
    )


# ---------------------------------------------------------------------------
# Dispatcher: pick the right template based on verdict + ask_count
# ---------------------------------------------------------------------------

async def build_ask_body(
    *,
    verdict_kind: str,
    ask_count: int,
    customer_name: str,
    customer_messages: list[str],
    model: str,
    api_key: str,
) -> tuple[str, str]:
    """给 trigger_consumer 用：按 verdict + ask_count 选模板生成 body。

    Returns:
        (body_text, template_name) — template_name 是 "A"/"B"/"C"/"D" 用于 log
    """
    template_name = _ask_template_name(
        verdict_kind=verdict_kind,
        ask_count=ask_count,
    )
    body = await _generate_ask_body_with_llm(
        verdict_kind=verdict_kind,
        ask_count=ask_count,
        customer_name=customer_name,
        customer_messages=customer_messages,
        model=model,
        api_key=api_key,
    )
    if body is not None:
        return body, template_name
    return _fallback_ask_body(
        verdict_kind=verdict_kind,
        ask_count=ask_count,
        customer_name=customer_name,
    )
