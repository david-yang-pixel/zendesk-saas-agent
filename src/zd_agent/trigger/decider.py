"""Trigger decider: LLM-based classifier for incoming customer messages.

Uses a lightweight OpenAI model (typically gpt-5.4-mini) with JSON-object
response format to categorize the last few customer messages as:
  - greeting           (pure hi/are-you-there)
  - insufficient_info  (has question but missing wid/error/steps)
  - substantial_issue  (actionable, enough context to start debugging)

Plus a completeness score (0-100).
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

判断客户是否已经陈述了一个具体的技术问题或需求。返回三种之一：

- "greeting": 纯打招呼/寒暄（"hi" / "hello" / "are you there" / "any update?" 等）
- "insufficient_info": 说了问题但关键信息缺失（提到"报错"但没贴错误、没给 Workspace ID、没说具体功能点）
- "substantial_issue": 已陈述具体问题 + 足够关键信息可以开始排查

同时给出完整度 completeness（0-100）：
- 0-30: 没说具体问题
- 30-60: 有模糊问题陈述，信息缺
- 60-80: 有问题陈述，信息基本齐
- 80-100: 信息非常完整

只返回 JSON（无额外文字）：
{{"verdict": "...", "completeness": 数字, "reason": "简短理由"}}"""


@dataclass
class PreCheckVerdict:
    verdict: Verdict
    completeness: int
    reason: str


async def _call_llm(
    *, prompt: str, model: str, api_key: str, timeout: float = 30.0,
) -> str:
    """Call OpenAI chat.completions, return the raw content string."""
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
                "temperature": 0.0,
                "max_tokens": 200,
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
    *, messages: list[str], model: str, api_key: str,
) -> PreCheckVerdict:
    """Classify the most recent customer messages via LLM.

    Sends only the last 5 messages to keep the prompt short.
    Falls back to 'insufficient_info' with completeness=50 on any error.
    """
    if not messages:
        return PreCheckVerdict(
            verdict="insufficient_info",
            completeness=0,
            reason="no customer messages",
        )

    recent = messages[-5:]
    joined = "\n---\n".join(recent)
    prompt = _PROMPT_TEMPLATE.format(messages=joined)

    try:
        raw = await _call_llm(prompt=prompt, model=model, api_key=api_key)
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
