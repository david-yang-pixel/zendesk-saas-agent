"""Trigger decider: LLM-based classifier for incoming customer messages.

Uses a lightweight OpenAI model (typically gpt-5.4-mini) with JSON-object
response format to categorize the last few customer messages.

Verdict types (6 total)
-----------------------
- **no_message**       — 没有任何客户文字消息（只 formResponse）。仅当 messages
                         空 + 无截图 时返回。
- **greeting**         — 纯打招呼/寒暄（"hi" / "在吗" / "any update?"）
- **insufficient_info** — 说了问题但缺关键信息（错误细节 / 复现步骤等）
- **substantial_issue** — 已陈述具体问题 + 足够上下文可以开始排查
- **request_human**    — 客户明确要求转人工（"human" / "人工" / "找真人"）
                         最高优先级中断信号。
- **self_resolved**    — 客户表达问题已自行解决 / 不需再帮助
                         （"已解决" / "thanks, fixed" / "nevermind"）

High-risk category（与 verdict 正交的附加 flag）
-----------------------------------------------
某些 substantial_issue 工单**强制走人工 review**，不允许 agent 自动公开回复：

- **refund**    — 涉及退款 / 计费争议（金额承诺不可撤回）
- **emotional** — 客户情绪激动（投诉 / 气愤 / 失望）
- **incident**  — 客户报"平台普遍故障"信号（需要统一对外口径，agent 不能随便说）

非高风险（normal）默认 ``high_risk_category=None``。

Vision
------
当 ``image_data_urls`` 非空时，请求作为 multimodal 发出，LLM 看图判断。

Fast-path
---------
为了省 LLM 调用：客户最近一条消息**完全等于**已知 human 触发词
（"human" / "人工" / "真人" / "找人" / "live agent"）时直接返回 ``request_human``，
不走 LLM。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import httpx

from zd_agent.log import get_logger

logger = get_logger(module="trigger.decider")

# 全部 6 个 verdict 类型
Verdict = Literal[
    "no_message",
    "greeting",
    "insufficient_info",
    "substantial_issue",
    "request_human",
    "self_resolved",
]

_VALID_VERDICTS: frozenset[str] = frozenset(
    Verdict.__args__,  # type: ignore[attr-defined]
)

# High-risk category（agent 不能自动公开回复，必须人工 review）
HighRiskCategory = Literal["refund", "emotional", "incident"]
_VALID_HIGH_RISK: frozenset[str] = frozenset(HighRiskCategory.__args__)  # type: ignore

# Fast-path keywords：客户最近一条消息精确匹配（去空格 + 大小写不敏感）→ request_human
# 这些是 footer 里明示告诉客户能用的，必须 100% 检出。
_HUMAN_FAST_PATH_KEYWORDS: frozenset[str] = frozenset({
    "human",
    "人工",
    "找人工",
    "转人工",
    "需要人工",
    "真人",
    "找真人",
    "live agent",
    "real person",
    "speak to human",
    "talk to human",
})


_PROMPT_TEMPLATE = """你是 Dify SaaS support 的触发判定模块。客户在 Zendesk 工单上发了以下消息（按时间升序）：

<客户消息>
{messages}
</客户消息>
{image_hint}
判断客户消息属于以下哪一种 verdict（六选一）：

1. **"no_message"**：没有任何文字消息（仅在客户消息列表为空 + 没有截图时返回，理论上你看到此 prompt 不太会命中）
2. **"greeting"**：纯打招呼/寒暄（"hi" / "hello" / "在吗" / "any update?" / 表情符等）
3. **"insufficient_info"**：说了问题但关键信息缺失（提"报错"没贴错误 / 没说具体功能点 / 描述太模糊）
4. **"substantial_issue"**：已陈述具体问题 + 足够关键信息可以开始排查
5. **"request_human"**：客户明确要求转人工或希望人参与
   - 例："human" / "人工" / "找真人" / "let me talk to someone real" / "I need a person to help me"
   - 注意：客户**仅仅说"急"或"在吗"**不算请求人工，那是 greeting
   - 此 verdict **优先级最高**：哪怕同一条消息也描述了问题，只要客户明示要人工，就归此类
6. **"self_resolved"**：客户表达问题已解决 / 不需再帮助
   - 例："已经解决了" / "thanks, figured it out" / "nevermind" / "不用了" / "no longer needed"

如果客户附了截图（图片消息）：
- 仔细观察截图（错误弹窗、报错堆栈、页面状态、聊天记录等）
- 哪怕文字少，截图清晰说明问题 → substantial_issue
- 截图模糊 / 看不清重点 → insufficient_info

同时给出 completeness（0-100）：
- 0-30: 没说具体问题
- 30-60: 有模糊问题陈述，信息缺
- 60-80: 有问题陈述，信息基本齐
- 80-100: 信息非常完整

**另外** 判断 high_risk_category（agent 不能自动公开回复，需人工 review 的工单）：
- **"refund"**：涉及退款 / 计费争议 / 金额讨论（"refund me" / "退我钱" / "为什么扣了 $X"）
- **"emotional"**：客户情绪激动 / 投诉 / 气愤 / 失望（"this is unacceptable" / "你们产品太差了" / 多个感叹号 / 脏话）
- **"incident"**：客户报疑似平台普遍故障（"你们服务器是不是挂了" / "everyone in my team can't login" / 提到"批量"/"所有人都"等普遍性词汇）
- 都不属于 → 填 `null`（**不要瞎贴 high_risk，宁可漏判**）

只返回 JSON（无额外文字）：
{{
  "verdict": "...",
  "completeness": 数字,
  "high_risk_category": "refund"|"emotional"|"incident"|null,
  "reason": "简短理由（中文，<=80字。如用了截图说明截图里看到啥；如标了 high_risk 也说原因）"
}}"""


@dataclass
class PreCheckVerdict:
    verdict: Verdict
    completeness: int
    reason: str
    # 与 verdict 正交：substantial_issue 但需强制人工的高风险标记
    high_risk_category: HighRiskCategory | None = None
    high_risk_reason: str = ""


def _detect_request_human_fast(messages: list[str]) -> bool:
    """Fast-path：客户最近一条消息**完全等于**已知 human 触发词。

    去掉首尾空格 + 标点 + 大小写不敏感后做精确匹配。
    精确匹配是为了**避免误判**——客户说"the human resources page is broken"
    不能误为"想要 human"。模糊匹配交给 LLM。
    """
    if not messages:
        return False
    last = messages[-1].strip().strip(".!?,。！？，").lower()
    return last in _HUMAN_FAST_PATH_KEYWORDS


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
                "max_completion_tokens": 400,
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
    """Classify the most recent customer messages via LLM (with fast-path).

    Fast-path（不走 LLM）：
      - messages 空 + image_data_urls 空 → ``no_message``
      - 最近一条消息精确匹配 human keyword → ``request_human``

    LLM path：发最近 5 条消息（+ 可选截图）给模型分类，拿到
    verdict / completeness / high_risk_category / reason。

    所有错误路径 fallback 到 ``insufficient_info`` + completeness=50。
    """
    image_data_urls = image_data_urls or []

    # Fast-path 1: 完全没消息也没图
    if not messages and not image_data_urls:
        return PreCheckVerdict(
            verdict="no_message",
            completeness=0,
            reason="no customer messages",
        )

    # Fast-path 2: 客户最近一条 = human keyword
    if _detect_request_human_fast(messages):
        last = messages[-1].strip()
        logger.info("verdict_fast_path_request_human", last_message=last[:60])
        return PreCheckVerdict(
            verdict="request_human",
            completeness=100,  # 客户表达很明确，"完整度" 100
            reason=f"customer fast-path request: {last[:50]!r}",
        )

    # 主路径：LLM 分类
    recent = messages[-5:] if messages else []
    joined = "\n---\n".join(recent) if recent else "(无文字消息)"
    image_hint = (
        f"\n\n客户还附了 {len(image_data_urls)} 张截图（已附在下方，请你看图判断）。\n"
        if image_data_urls
        else ""
    )
    prompt_text = _PROMPT_TEMPLATE.format(messages=joined, image_hint=image_hint)

    if image_data_urls:
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

    # 解析 verdict（兜底到 insufficient_info）
    verdict = parsed.get("verdict", "insufficient_info")
    if verdict not in _VALID_VERDICTS:
        logger.warning("pre_check_unknown_verdict", raw_verdict=verdict)
        verdict = "insufficient_info"

    # 解析 completeness（夹在 [0, 100]）
    try:
        completeness = int(parsed.get("completeness", 50))
    except (TypeError, ValueError):
        completeness = 50
    completeness = max(0, min(100, completeness))

    # 解析 high_risk_category（None / refund / emotional / incident）
    high_risk_raw = parsed.get("high_risk_category")
    high_risk: HighRiskCategory | None = None
    if high_risk_raw and isinstance(high_risk_raw, str):
        cat = high_risk_raw.strip().lower()
        if cat in _VALID_HIGH_RISK:
            high_risk = cat  # type: ignore[assignment]
        elif cat not in ("null", "none", ""):
            logger.warning("pre_check_unknown_high_risk", raw=high_risk_raw)

    reason = str(parsed.get("reason", ""))

    return PreCheckVerdict(
        verdict=verdict,  # type: ignore[arg-type]
        completeness=completeness,
        reason=reason,
        high_risk_category=high_risk,
        high_risk_reason=reason if high_risk else "",
    )
