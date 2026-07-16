"""Pre-send freshness check：发公开回复前检查客户有没有发新的实质消息。

为什么需要这个
--------------
agent 分析工单到决定 reply 期间，客户可能继续打字补充信息（典型 messaging
场景：客户先发"应用挂了"，30 秒后发截图，1 分钟后又发 workspace id）。

如果 agent 在客户补完信息前就发出 ask 模板"请提供 workspace id"，会让 agent
显得**完全没看上下文** → 客户烦 + agent 体验崩。

防护机制
--------
1. **Snapshot 客户消息状态**（count + last_id）保存到 AgentSession，时刻是
   agent dispatch 起跑时
2. **发送前 fetch 一次 + 比对** snapshot，如果客户加了新消息：
   - 用 ``is_substantive`` 判断这条新消息是不是"实质性"
   - 实质性（截图 / UUID / 较长文本等）→ abort 当前发送，让 trigger_consumer 重新评估
   - 非实质性（"谢谢" / "OK" / "在吗"等催促）→ 继续发原回复

is_substantive 混合判断
-----------------------
- 硬规则（覆盖 80% 场景，0ms 0 cost）：
    - 客户附图 → True
    - 含 UUID / URL / 代码块 → True
    - 长文本 (>100 字符) → True
    - 在 IGNORE_LIST 里（"谢谢" / "OK" / "thanks" 等）→ False
- 灰区（短文本不在 IGNORE_LIST）→ LLM 判断（cheap model, ~$0.0001 / 500ms）

设计来自 2026-05-13 跟用户讨论。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

import httpx

from zd_agent.log import get_logger
from zd_agent.runtime.openai_compat import chat_completions_url
from zd_agent.runtime.openai_reasoning import (
    reasoning_completion_token_limit,
    reasoning_timeout_seconds,
)

logger = get_logger(module="runtime.pre_send_check")


# ---------------------------------------------------------------------------
# 公共数据结构
# ---------------------------------------------------------------------------

class FreshnessRecoveryAction(str, Enum):
    """Typed next step for a failed customer-message freshness check."""

    REFRESH_CONTEXT = "refresh_context"
    RETRY_FETCH = "retry_fetch"
    DEFER = "defer"


@dataclass(frozen=True)
class FreshnessRecovery:
    """Runtime action required before another public-reply attempt."""

    action: FreshnessRecoveryAction
    new_messages: tuple[dict, ...] = ()
    next_snapshot_count: int = 0
    next_snapshot_last_id: str = ""
    reason: str = ""


@dataclass
class FreshnessResult:
    """pre-send check 的结果。

    ``continue_send=True``  → 没新消息 / 新消息都不实质 → 可以发原 reply
    ``continue_send=False`` → 有实质性新消息 → abort 当前 reply，重新走 trigger
    """
    continue_send: bool
    reason: str = ""
    new_messages_count: int = 0
    recovery: FreshnessRecovery | None = None


# ---------------------------------------------------------------------------
# Heuristic：硬规则覆盖 80% 场景
# ---------------------------------------------------------------------------

# 客户的"非实质"短消息：寒暄 / 确认 / 催促 / 表情符
# 这些不应该让 agent abort 重新分析（continue_send=True）
_IGNORE_LIST: frozenset[str] = frozenset({
    # 中文
    "谢谢", "谢谢了", "thx", "好的", "好", "嗯", "嗯嗯", "ok",
    "在吗", "急", "急急急", "请问还在吗", "您还在吗",
    "不用了", "算了",
    # English
    "thanks", "thank you", "ty", "thx", "got it", "ok",
    "okay", "k", "alright", "any update", "any update?",
    "still there?", "you there?", "hello?", "hi?",
    # 表情符 / 单字
    "👍", "👌", "✅", "🥲", "🙏", "😅",
})

# 视为"实质性"的硬规则匹配模式
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+")
_CODE_BLOCK_RE = re.compile(r"```|^    \S", re.MULTILINE)
_ERROR_TRACE_RE = re.compile(
    r"\b(error|exception|traceback|stack trace|"
    r"\d{3} (Internal Server Error|Bad Request|Not Found))\b",
    re.IGNORECASE,
)

# 长文本阈值（字符数）。超过 → 实质性
_LONG_TEXT_THRESHOLD: int = 100


def is_substantive_heuristic(msg: dict) -> bool | None:
    """硬规则判断客户新消息是否实质性。

    Returns:
        True  — 实质性（agent 应 abort 重新分析）
        False — 非实质性（agent 继续发原 reply）
        None  — 灰区，让 LLM 判断（短文本不在 IGNORE_LIST）
    """
    # 1. 附图 → 实质（screenshot / 错误截图 / 配置截图等都算补充上下文）
    image_urls = msg.get("image_urls") or []
    if image_urls:
        return True

    body = (msg.get("body") or "").strip()

    if not body:
        # 没文字 + 没图 = 空消息，不算实质
        return False

    # 2. IGNORE_LIST 精确命中 → 非实质
    body_normalized = body.strip().strip(".!?,。！？，").lower()
    if body_normalized in _IGNORE_LIST:
        return False

    # 3. 含 UUID → 实质（workspace_id / app_id / run_id 等）
    if _UUID_RE.search(body):
        return True

    # 4. 含 URL → 实质（截图链接 / 文档 / 复现链接等）
    if _URL_RE.search(body):
        return True

    # 5. 含 代码块 / 错误堆栈 → 实质
    if _CODE_BLOCK_RE.search(body) or _ERROR_TRACE_RE.search(body):
        return True

    # 6. 长文本 → 实质
    if len(body) > _LONG_TEXT_THRESHOLD:
        return True

    # 7. 灰区：短文本（< 100 字符）且不在 IGNORE_LIST
    return None


# ---------------------------------------------------------------------------
# LLM judge：灰区 fallback
# ---------------------------------------------------------------------------

_LLM_PROMPT_TEMPLATE = """你判断客户在工单里新发的这条短消息是不是"实质性新信息"（agent 应该重新分析）还是"非实质性的寒暄/确认/催促"。

客户消息：
<msg>
{body}
</msg>

判断标准：
- **实质性**（substantive=true）：
  - 补充了具体错误信息 / 复现步骤 / 业务上下文
  - 提供了具体的 ID / 数字 / 时间点
  - 描述了新的故障点
- **非实质性**（substantive=false）：
  - 仅是寒暄 / 确认 / 客套（"谢谢"、"OK"、"还在吗"、"急"）
  - 催促但没新信息
  - 表情符 / 单字回复

只返回 JSON（无额外文字）：{{"substantive": true|false, "reason": "<20字简短理由>"}}"""


async def is_substantive_llm(
    body: str,
    *,
    model: str,
    api_key: str,
    reasoning_effort: str = "",
    base_url: str | None = None,
    timeout: float = 8.0,
) -> bool:
    """LLM 判断短文本灰区消息是否实质性。

    出错（超时 / API 失败 / 解析失败）默认返回 ``True``（保守，宁可 abort 重新分析）。
    """
    prompt = _LLM_PROMPT_TEMPLATE.format(body=body[:500])

    try:
        request_json = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": reasoning_completion_token_limit(
                60,
                reasoning_effort,
            ),
            "response_format": {"type": "json_object"},
        }
        if reasoning_effort:
            request_json["reasoning_effort"] = reasoning_effort
        effective_timeout = reasoning_timeout_seconds(timeout, reasoning_effort)
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            resp = await client.post(
                chat_completions_url(base_url),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_json,
            )
    except Exception as e:
        logger.warning("pre_send_llm_http_error", error=str(e)[:200])
        return True  # 保守：把灰区当实质

    if resp.status_code != 200:
        logger.warning(
            "pre_send_llm_bad_status",
            status=resp.status_code, body=resp.text[:200],
        )
        return True

    try:
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        substantive = bool(parsed.get("substantive", True))
        logger.info(
            "pre_send_llm_judge",
            substantive=substantive,
            reason=parsed.get("reason", "")[:60],
            body_preview=body[:60],
        )
        return substantive
    except Exception as e:
        logger.warning("pre_send_llm_parse_error", error=str(e)[:200])
        return True


# ---------------------------------------------------------------------------
# 主入口：pre_send_check_or_abort
# ---------------------------------------------------------------------------

# 客户消息 fetch 函数签名：返回 list[dict]，每个 dict 至少有 "id" + "body" + 可选 "image_urls"
FetchMessagesFn = Callable[[], Awaitable[list[dict]]]


async def pre_send_check_or_abort(
    *,
    ticket_id: int,
    snapshot_count: int,
    snapshot_last_id: str | None,
    fetch_messages_fn: FetchMessagesFn,
    model: str = "gpt-5.4-mini",
    api_key: str = "",
    reasoning_effort: str = "",
    base_url: str | None = None,
    skip_llm: bool = False,
) -> FreshnessResult:
    """发送前再 fetch 客户消息 + 检查是否新增实质性消息。

    Args:
        ticket_id: 工单号（用于 log）
        snapshot_count: agent dispatch 起跑时的客户消息条数
        snapshot_last_id: agent dispatch 起跑时的最后一条客户消息 id
        fetch_messages_fn: async 函数，返回**最新**的客户消息列表
        model: LLM judge 用的模型（默认 gpt-5.4-mini）
        api_key: OpenAI api key
        skip_llm: True 时灰区按 True 保守处理（不调 LLM），用于离线测试

    Returns:
        FreshnessResult(continue_send=True)  → OK 发原 reply
        FreshnessResult(continue_send=False) → abort，由调用方决定后续动作
    """
    try:
        current = await fetch_messages_fn()
    except Exception as e:
        logger.warning(
            "pre_send_fetch_error",
            ticket_id=ticket_id, error_type=type(e).__name__,
        )
        return FreshnessResult(
            continue_send=False,
            reason=f"fetch_error: {type(e).__name__}",
            recovery=FreshnessRecovery(
                action=FreshnessRecoveryAction.RETRY_FETCH,
                reason=f"fetch_error: {type(e).__name__}",
            ),
        )

    current_count = len(current)
    new_msgs: list[dict]
    if snapshot_last_id:
        snapshot_index: int | None = None
        for idx, msg in enumerate(current):
            if str(msg.get("id") or "") == str(snapshot_last_id):
                snapshot_index = idx
        if snapshot_index is None:
            logger.warning(
                "pre_send_snapshot_last_id_missing",
                ticket_id=ticket_id,
                snapshot_last_id=snapshot_last_id,
                snapshot_count=snapshot_count,
                current_count=current_count,
            )
            return FreshnessResult(
                continue_send=False,
                reason="snapshot_last_id_missing",
                new_messages_count=current_count,
                recovery=FreshnessRecovery(
                    action=FreshnessRecoveryAction.DEFER,
                    next_snapshot_count=current_count,
                    next_snapshot_last_id=(
                        str(current[-1].get("id") or "") if current else ""
                    ),
                    reason="snapshot_last_id_missing",
                ),
            )
        new_msgs = current[snapshot_index + 1:]
    else:
        if current_count <= snapshot_count:
            # 没新消息（或者甚至变少了，少见但容错）
            return FreshnessResult(
                continue_send=True,
                reason="no_new_messages",
                new_messages_count=0,
            )
        new_msgs = current[snapshot_count:]

    new_count = len(new_msgs)
    if new_count <= 0:
        return FreshnessResult(
            continue_send=True,
            reason="no_new_messages",
            new_messages_count=0,
        )
    logger.info(
        "pre_send_new_msgs_detected",
        ticket_id=ticket_id, new_count=new_count,
        snapshot_count=snapshot_count, current_count=current_count,
    )

    recovery = FreshnessRecovery(
        action=FreshnessRecoveryAction.REFRESH_CONTEXT,
        new_messages=tuple(new_msgs),
        next_snapshot_count=current_count,
        next_snapshot_last_id=(
            str(current[-1].get("id") or "") if current else ""
        ),
    )

    # 逐条评估新消息，遇任何一条实质性立刻 abort
    for i, msg in enumerate(new_msgs):
        verdict = is_substantive_heuristic(msg)

        if verdict is True:
            logger.info(
                "pre_send_abort_heuristic",
                ticket_id=ticket_id, msg_index=i,
                body_preview=(msg.get("body") or "")[:80],
                has_image=bool(msg.get("image_urls")),
            )
            return FreshnessResult(
                continue_send=False,
                reason="substantive_heuristic",
                new_messages_count=new_count,
                recovery=recovery,
            )

        if verdict is None:
            # 灰区 → LLM 判断
            if skip_llm:
                # 离线 / 测试场景：保守按实质处理
                return FreshnessResult(
                    continue_send=False,
                    reason="substantive_llm_skipped",
                    new_messages_count=new_count,
                    recovery=recovery,
                )

            body = (msg.get("body") or "").strip()
            substantive = await is_substantive_llm(
                body,
                model=model,
                api_key=api_key,
                reasoning_effort=reasoning_effort,
                base_url=base_url,
            )
            if substantive:
                logger.info(
                    "pre_send_abort_llm",
                    ticket_id=ticket_id, msg_index=i,
                    body_preview=body[:80],
                )
                return FreshnessResult(
                    continue_send=False,
                    reason="substantive_llm",
                    new_messages_count=new_count,
                    recovery=recovery,
                )
        # verdict is False or LLM said non-substantive → 继续检查下一条

    # 所有新消息都是 non-substantive（寒暄 / 催促 / emoji）→ OK 发原 reply
    logger.info(
        "pre_send_continue_all_non_substantive",
        ticket_id=ticket_id, new_count=new_count,
    )
    return FreshnessResult(
        continue_send=True,
        reason="all_new_msgs_non_substantive",
        new_messages_count=new_count,
        # The reply can proceed without a context rebase, but the caller must
        # persist this accepted cursor once delivery succeeds.  Otherwise a
        # delayed webhook will treat the same "thanks" / "any update" as new
        # work and dispatch the ticket again.
        recovery=recovery,
    )
