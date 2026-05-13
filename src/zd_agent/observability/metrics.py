"""Public reply 监控指标埋点（M8）。

每个 emit 函数发一条 structlog event，统一以 ``metric.`` 前缀，方便 SigNoz
按 event name 聚合 + 出图。

4 个核心指标（与 2026-05-13 设计文档一致）：

1. **customer_requested_human** — 客户主动喊"human"要求转人工
   - 反映 agent 公共回复的"客户拒绝率"
   - 触发：``_set_escalated_to_human(reason="customer_requested")``
   - SigNoz 看板：按天计数 / 与 ``agent_public_reply_sent`` 算占比

2. **agent_public_reply_sent** — agent 成功发出一条公共回复
   - 用作其它 rate 类指标的分母
   - 触发：``send_public_reply()`` / ``ZendeskPublicReply._run()`` 成功之后
   - **不**包含 footer 的字符数（footer 是固定加上去的）

3. **customer_reopen** — 工单已 processed 后客户又回来发消息
   - 反映"agent 自以为解决了，但客户其实没"
   - 触发：``_trigger_consumer`` 顶部，当 state == processed 且新增客户消息
   - 注意：state == escalated_to_human 时不算 reopen（人工已接管）

4. **abandoned** — 24h 内客户彻底无 actionable 回应
   - 反映 agent ask 过多 / ask 没问到点子上
   - 触发：``waiting_poller`` 把 state 转成 abandoned 时

另注：``agent_modify_rate``（人工 SE 修改了 agent 的回复占比）需要后续
ticket comments 轮询任务（对照 agent 公共回复发出时间 vs SE 后续是否
follow-up），M8 仅埋了 ``agent_public_reply_sent``（分母）+ 留 hook，
后续 M9/M10 加 modify 检测。
"""
from __future__ import annotations

from typing import Optional

from zd_agent.log import get_logger

logger = get_logger(module="observability.metrics")


def emit_customer_requested_human(
    *,
    ticket_id: int | str,
    source: str = "unknown",
    customer_phrase: str = "",
    ask_count_so_far: int = 0,
) -> None:
    """客户喊 'human' / '人工' / 'talk to a human' 要求转人工。

    Args:
        ticket_id: 工单 id
        source: trigger source（webhook / silence_waker / ...），帮 SigNoz 拆出
            哪个入口检测出来的
        customer_phrase: 客户原话（截前 120 字符，去除 PII 噪声）
        ask_count_so_far: 升级前已经 ask 客户多少次，反映"客户在第几轮放弃"
    """
    logger.info(
        "metric.customer_requested_human",
        ticket_id=str(ticket_id),
        source=source,
        customer_phrase=(customer_phrase or "")[:120],
        ask_count_so_far=ask_count_so_far,
    )


def emit_agent_public_reply_sent(
    *,
    ticket_id: int | str,
    reply_type: str,
    template_name: str = "",
    body_chars: int = 0,
) -> None:
    """Agent 成功发出一条公共回复（含 ask / solution / handoff / closed 全部类型）。

    Args:
        ticket_id: 工单 id
        reply_type: ``ask`` / ``solution`` / ``handoff`` / ``closed``
        template_name: 模板代号 ``A``/``B``/``C``/``D``/``E``/``G``，便于看每类模板
            被发了多少次（C/D 是问 vague 信息的两次/多次 ask）
        body_chars: body 字符数（不含 footer），观测回复长度分布用
    """
    logger.info(
        "metric.agent_public_reply_sent",
        ticket_id=str(ticket_id),
        reply_type=reply_type,
        template_name=template_name,
        body_chars=body_chars,
    )


def emit_customer_reopen(
    *,
    ticket_id: int | str,
    prev_state: str,
    source: str,
    hours_since_processed: Optional[float] = None,
) -> None:
    """工单已经 processed / closed 后，客户又来发消息。

    Args:
        ticket_id: 工单 id
        prev_state: reopen 前的 state（通常是 ``processed``）
        source: 触发 reopen 的 entry source
        hours_since_processed: agent 标 processed 到 reopen 的小时数（None 表示
            没有 last_eval_at 数据）
    """
    logger.info(
        "metric.customer_reopen",
        ticket_id=str(ticket_id),
        prev_state=prev_state,
        source=source,
        hours_since_processed=(
            round(hours_since_processed, 2)
            if hours_since_processed is not None else None
        ),
    )


def emit_abandoned(
    *,
    ticket_id: int | str,
    age_hours: float,
    last_verdict: str = "",
    ask_count: int = 0,
) -> None:
    """24h 客户彻底无 actionable 回应 → 标为 abandoned。

    Args:
        ticket_id: 工单 id
        age_hours: 从 first_seen 到 abandoned 的总小时数
        last_verdict: 最后一次 trigger_decider 给出的 verdict
            （greeting / insufficient_info / no_message 等）
        ask_count: 累计 ask 客户的次数（反映 agent 努力但没问到点子上）
    """
    logger.info(
        "metric.abandoned",
        ticket_id=str(ticket_id),
        age_hours=round(age_hours, 2),
        last_verdict=last_verdict,
        ask_count=ask_count,
    )
