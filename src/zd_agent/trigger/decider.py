"""Trigger decider: LLM-based classifier for incoming customer messages.

Uses a lightweight OpenAI model (typically gpt-5.4-mini) with JSON-object
response format to categorize the last few customer messages as:
  - greeting           (pure hi/are-you-there)
  - insufficient_info  (has question but missing wid/error/steps)
  - substantial_issue  (actionable, enough context to start debugging)

Plus a completeness score (0-100).

Supports vision: when ``image_data_urls`` are passed, the messages are sent
as a multimodal user content block (text + image_url parts) so the model can
look at customer-attached screenshots when judging completeness.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import httpx

from zd_agent.log import get_logger

logger = get_logger(module="trigger.decider")

Verdict = Literal["greeting", "insufficient_info", "substantial_issue"]

_PROMPT_TEMPLATE = """你是 Dify SaaS support 的触发判定模块。客户在 Zendesk 工单上发了以下消息（按时间升序）：

<客户消息>
{messages}
</客户消息>
{image_hint}
判断客户是否已经陈述了一个具体的技术问题或需求。返回三种之一：

- "greeting": 纯打招呼/寒暄（"hi" / "hello" / "are you there" / "any update?" 等）
- "insufficient_info": 说了问题但关键信息缺失（提到"报错"但没贴错误、没给 Workspace ID、没说具体功能点）
- "substantial_issue": 已陈述具体问题 + 足够关键信息可以开始排查

如果客户附了截图（图片消息）：
- 请仔细观察截图内容（错误弹窗、报错堆栈、页面状态、配置信息、聊天记录等）
- 哪怕文字消息很少，只要截图能清晰说明问题（例如 "Workflow 执行失败 + 报错截图"），也应该判 substantial_issue
- 截图模糊、看不清重点、或与问题无关 → insufficient_info

同时给出完整度 completeness（0-100）：
- 0-30: 没说具体问题
- 30-60: 有模糊问题陈述，信息缺
- 60-80: 有问题陈述，信息基本齐
- 80-100: 信息非常完整

只返回 JSON（无额外文字）：
{{"verdict": "...", "completeness": 数字, "reason": "简短理由（中文，<=80字，若用了截图请说明截图里看到了什么）"}}"""


@dataclass
class PreCheckVerdict:
    verdict: Verdict
    completeness: int
    reason: str


async def _call_llm(
    *,
    user_content,
    model: str,
    api_key: str,
    timeout: float = 60.0,
) -> str:
    """Call OpenAI chat.completions, return the raw content string.

    ``user_content`` may be either a plain string (text-only) or a list of
    multimodal parts (text + image_url) per OpenAI vision spec.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": user_content}],
                # gpt-5.4 系列不支持 temperature / max_tokens，用 max_completion_tokens
                "max_completion_tokens": 300,
                "response_format": {"type": "json_object"},
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"LLM call failed: {resp.status_code} {resp.text[:300]}"
        )
    data = resp.json()
    return data["choices"][0]["message"]["content"]


async def classify_messages(
    *,
    messages: list[str],
    model: str,
    api_key: str,
    image_data_urls: list[str] | None = None,
) -> PreCheckVerdict:
    """Classify the most recent customer messages via LLM.

    Sends only the last 5 messages to keep the prompt short.
    If ``image_data_urls`` is non-empty, the call is made as a multimodal
    request so the LLM can also look at customer-attached screenshots.
    Falls back to 'insufficient_info' with completeness=50 on any error.
    """
    if not messages and not image_data_urls:
        return PreCheckVerdict(
            verdict="insufficient_info",
            completeness=0,
            reason="no customer messages",
        )

    recent = messages[-5:] if messages else []
    joined = "\n---\n".join(recent) if recent else "(无文字消息)"
    image_data_urls = image_data_urls or []
    image_hint = (
        f"\n\n客户还附了 {len(image_data_urls)} 张截图（已附在下方，请你看图判断）。\n"
        if image_data_urls
        else ""
    )
    prompt_text = _PROMPT_TEMPLATE.format(messages=joined, image_hint=image_hint)

    if image_data_urls:
        # Multimodal: prompt text + image parts
        parts: list[dict] = [{"type": "text", "text": prompt_text}]
        for url in image_data_urls:
            parts.append({
                "type": "image_url",
                "image_url": {"url": url},
            })
        user_content = parts
    else:
        user_content = prompt_text

    try:
        raw = await _call_llm(
            user_content=user_content, model=model, api_key=api_key,
        )
    except Exception as e:
        logger.warning("pre_check_llm_error", error=str(e))
        return PreCheckVerdict(
            verdict="insufficient_info",
            completeness=50,
            reason=f"LLM error: {e}",
        )

    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warning("pre_check_invalid_json", raw=raw[:200])
        return PreCheckVerdict(
            verdict="insufficient_info",
            completeness=50,
            reason="invalid JSON from LLM",
        )

    verdict = parsed.get("verdict", "insufficient_info")
    if verdict not in ("greeting", "insufficient_info", "substantial_issue"):
        verdict = "insufficient_info"

    try:
        completeness = int(parsed.get("completeness", 50))
    except (TypeError, ValueError):
        completeness = 50
    completeness = max(0, min(100, completeness))

    reason = str(parsed.get("reason", ""))

    return PreCheckVerdict(
        verdict=verdict,
        completeness=completeness,
        reason=reason,
    )
