"""Trigger decider: LLM-based classifier for incoming customer messages.

Uses a lightweight OpenAI model (typically gpt-5.4-mini) with JSON-object
response format to categorize the last few customer messages.

Verdict types (6 total)
-----------------------
- **no_message**       — 没有任何客户文字消息（只 formResponse）。仅当 messages
                         空 + 无截图 时返回。
- **greeting**         — 纯打招呼/寒暄（"hi" / "在吗" / "any update?"）
- **insufficient_info** — 还无法判断客户具体要解决什么
- **substantial_issue** — 已表达可处理诉求/问题，可以进入 agent 排查或回答
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
from zd_agent.runtime.openai_compat import chat_completions_url

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


_ACCOUNT_EMAIL_KEYWORDS = (
    "email address",
    "e-mail address",
    "mail address",
    "account email",
    "メールアドレス",
    "メール",
    "邮箱",
    "郵箱",
    "电子邮件",
    "電子郵件",
)
_ACCOUNT_CHANGE_KEYWORDS = (
    "change",
    "update",
    "modify",
    "replace",
    "変更",
    "更新",
    "改",
    "修改",
    "更换",
    "變更",
)
_FAILURE_SIGNAL_KEYWORDS = (
    "error",
    "getting an error",
    "can't",
    "cannot",
    "can not",
    "unable",
    "failed",
    "not able",
    "doesn't work",
    "does not work",
    "エラー",
    "できない",
    "出来ない",
    "失敗",
    "報錯",
    "报错",
    "无法",
    "不能",
    "失败",
)
_BILLING_REQUEST_KEYWORDS = (
    "refund",
    "charge",
    "charged",
    "invoice",
    "billing",
    "payment",
    "subscription renewal",
    "renewal",
    "退款",
    "退费",
    "扣费",
    "收费",
    "账单",
    "帳單",
    "发票",
    "發票",
    "付款",
    "续费",
    "續費",
)
_REFUND_RISK_KEYWORDS = (
    "refund",
    "charge",
    "charged",
    "payment failed",
    "unexpected charge",
    "overcharged",
    "退款",
    "退费",
    "扣费",
    "多扣",
    "乱扣",
    "未授权扣款",
    "未授權扣款",
)
_ACTIONABLE_DOMAIN_KEYWORDS = (
    "account",
    "api",
    "app",
    "application",
    "billing",
    "chatflow",
    "dataset",
    "document",
    "documents",
    "email",
    "invoice",
    "jina",
    "knowledge",
    "knowledge base",
    "kb",
    "model",
    "node",
    "plugin",
    "quota",
    "rerank",
    "subscription",
    "tool",
    "workflow",
    "workspace",
    "账号",
    "账户",
    "帳號",
    "应用",
    "應用",
    "插件",
    "模型",
    "知识库",
    "知識庫",
    "工作流",
    "文档",
    "文件",
    "账单",
    "帳單",
    "订阅",
    "訂閱",
    "额度",
    "积分",
)
_ACTIONABLE_REQUEST_PHRASES = (
    "can i",
    "can we",
    "could you",
    "how can i",
    "how do i",
    "how to",
    "i need",
    "i want",
    "please help",
    "please check",
    "what is",
    "where can i",
    "where do i",
    "why",
    "帮我",
    "幫我",
    "请问",
    "請問",
    "请帮",
    "請幫",
    "如何",
    "怎么",
    "怎麼",
    "为什么",
    "為什麼",
    "在哪里",
    "在哪裡",
    "能不能",
    "可以",
    "需要",
    "想要",
)
_GENERIC_HELP_ONLY_MESSAGES = frozenset({
    "can you help me",
    "could you help me",
    "help",
    "help me",
    "please help",
    "please help me",
    "i need help",
    "i have a question",
    "i have an issue",
    "there is a problem",
    "帮我看看",
    "幫我看看",
    "需要帮助",
    "需要幫助",
    "有问题",
    "有問題",
})


_PROMPT_TEMPLATE = """你是 Dify SaaS support 的触发判定模块。客户在 Zendesk 工单上发了以下完整消息上下文（按时间升序）：

<客户消息>
{messages}
</客户消息>
{image_hint}
请基于全部上下文判断当前工单属于以下哪一种 verdict（六选一）：

1. **"no_message"**：没有任何文字消息（仅在客户消息列表为空 + 没有截图时返回，理论上你看到此 prompt 不太会命中）
2. **"greeting"**：纯打招呼/寒暄（"hi" / "hello" / "在吗" / "any update?" / 表情符等）
3. **"insufficient_info"**：还无法判断客户具体要解决什么，只知道客户在打招呼、泛泛求助或说"有问题/帮我看看"但没有任何业务对象或诉求方向
4. **"substantial_issue"**：客户已经表达了可处理诉求/问题/请求，agent 可以进入处理链路
   - 包括退款/账单/订阅/积分、功能使用咨询、how-to、账号问题、API、Workflow、Knowledge Base、模型、插件、报错、卡住、缺失、不显示等
   - 即使缺少截图、错误全文、workspace id、复现步骤，也仍归 substantial_issue；agent 会在处理过程中按场景补问最小必要信息
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
- 0-30: 没说具体要解决什么
- 30-60: 只有模糊诉求，无法判断帮助方向
- 60-80: 已知道诉求/问题方向，可以开始处理但可能缺少部分细节
- 80-100: 信息非常完整

**另外** 判断 high_risk_category（agent 不能自动公开回复，需人工 review 的工单）：
- **"refund"**：涉及退款 / 计费争议 / 金额讨论（"refund me" / "退我钱" / "为什么扣了 $X"）
- **"emotional"**：客户情绪激动 / 投诉 / 气愤 / 失望（"this is unacceptable" / "你们产品太差了" / 多个感叹号 / 脏话）
- **"incident"**：客户报**多 workspace / 平台级故障 / SLA 类停服**（"你们服务器是不是挂了" / "Dify down 了吗"/ "所有 workspace 都 access 不了"）。
  - ⚠️ **客户说"影响整个组织 / 我们 team 多人" 不算** —— 那只是单 workspace 内多个用户，不是 platform-wide。
  - ⚠️ **客户说某个功能用不了 / 卡 0% / 报错** 也不算 incident（即使影响他多人）—— 这是单 workspace bug，让 agent 用 SigNoz 实际查再决定。
  - **真 incident 的关键词**：dify.ai / SaaS / 平台 / status page / 多个客户都报 / 全部组织等**跨 tenant** 信号。
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


def _should_upgrade_account_email_issue(messages: list[str]) -> bool:
    """Detect concrete account-email change failures that are actionable.

    The LLM prompt intentionally asks for exact error text when available, but
    account/email-change failures are already specific enough to begin
    investigation. Re-asking a generic "what is happening?" message makes the
    agent look like it ignored the customer.
    """
    if not messages:
        return False
    text = "\n".join(messages[-3:]).lower()
    has_email = any(keyword in text for keyword in _ACCOUNT_EMAIL_KEYWORDS)
    has_change = any(keyword in text for keyword in _ACCOUNT_CHANGE_KEYWORDS)
    has_failure = any(keyword in text for keyword in _FAILURE_SIGNAL_KEYWORDS)
    return has_email and has_change and has_failure


def _normalize_customer_text(messages: list[str]) -> str:
    text = "\n".join(messages[-3:]).lower()
    return " ".join(text.strip().strip(".!?,。！？，").split())


def _detect_actionable_request(messages: list[str]) -> str | None:
    if not messages:
        return None

    text = _normalize_customer_text(messages)
    if not text or text in _GENERIC_HELP_ONLY_MESSAGES:
        return None

    if _should_upgrade_account_email_issue(messages):
        return "account_email"

    if any(keyword in text for keyword in _BILLING_REQUEST_KEYWORDS):
        return "billing_request"

    has_domain = any(keyword in text for keyword in _ACTIONABLE_DOMAIN_KEYWORDS)
    has_failure = any(keyword in text for keyword in _FAILURE_SIGNAL_KEYWORDS)
    has_request = any(keyword in text for keyword in _ACTIONABLE_REQUEST_PHRASES)

    if has_domain and (has_failure or has_request):
        return "domain_request"
    if has_failure and len(text) >= 18:
        return "failure_signal"
    return None


def _detect_refund_risk(messages: list[str]) -> bool:
    text = _normalize_customer_text(messages)
    return any(keyword in text for keyword in _REFUND_RISK_KEYWORDS)


def _json_candidate(raw: str) -> str:
    text = raw.strip()
    lines = text.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().startswith("```")
        and lines[-1].strip() == "```"
    ):
        return "\n".join(lines[1:-1]).strip()
    return text


def _actionable_fallback_from_messages(
    messages: list[str],
    reason: str,
    *,
    error: str | None = None,
) -> PreCheckVerdict | None:
    actionable_kind = _detect_actionable_request(messages)
    if not actionable_kind:
        return None

    logger.info(
        "verdict_fallback_actionable_request",
        actionable_kind=actionable_kind,
        error=(error or "")[:200],
    )
    fallback_reason = (
        f"{reason}, but the latest customer messages already contain "
        "an actionable support request."
    )
    high_risk = "refund" if _detect_refund_risk(messages) else None
    return PreCheckVerdict(
        verdict="substantial_issue",
        completeness=65,
        reason=fallback_reason,
        high_risk_category=high_risk,
        high_risk_reason=fallback_reason if high_risk else "",
    )


async def _call_llm(
    *,
    user_content,
    model: str,
    api_key: str,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> str:
    """Call OpenAI chat.completions, return the raw content string.

    ``user_content`` may be either a plain string (text-only) or a list of
    multimodal parts (text + image_url) per OpenAI vision spec.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            chat_completions_url(base_url),
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
    base_url: str | None = None,
    image_data_urls: list[str] | None = None,
) -> PreCheckVerdict:
    """Classify the full customer-message context via LLM (with fast-path).

    Fast-path（不走 LLM）：
      - messages 空 + image_data_urls 空 → ``no_message``
      - 最近一条消息精确匹配 human keyword → ``request_human``

    LLM path：发全部客户消息上下文（+ 可选截图）给模型分类，拿到
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
    recent = messages if messages else []
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
            user_content=user_content,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
    except Exception as e:
        logger.warning("pre_check_llm_error", error=str(e))
        fallback = _actionable_fallback_from_messages(
            recent,
            "LLM error",
            error=str(e),
        )
        if fallback:
            return fallback
        return PreCheckVerdict(
            verdict="insufficient_info",
            completeness=50,
            reason=f"LLM error: {e}",
        )

    try:
        parsed = json.loads(_json_candidate(raw))
    except Exception:
        logger.warning("pre_check_invalid_json", raw=raw[:200])
        fallback = _actionable_fallback_from_messages(
            recent,
            "invalid JSON from LLM",
            error=raw,
        )
        if fallback:
            return fallback
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
    actionable_kind = _detect_actionable_request(recent)
    if verdict == "insufficient_info" and actionable_kind:
        logger.info(
            "verdict_override_actionable_request",
            previous_verdict=verdict,
            previous_completeness=completeness,
            actionable_kind=actionable_kind,
        )
        verdict = "substantial_issue"
        completeness = max(completeness, 65)
        if actionable_kind == "account_email":
            reason = (
                "客户已说明账号邮箱变更失败/报错，具备明确功能和失败现象，可开始排查；"
                "缺少错误文本可在处理中补问。"
            )
        else:
            reason = (
                "客户已表达明确诉求或问题方向，可进入 agent 处理；"
                "缺少细节可在处理中按场景补问。"
            )

    if high_risk is None and _detect_refund_risk(recent):
        high_risk = "refund"

    return PreCheckVerdict(
        verdict=verdict,  # type: ignore[arg-type]
        completeness=completeness,
        reason=reason,
        high_risk_category=high_risk,
        high_risk_reason=reason if high_risk else "",
    )
