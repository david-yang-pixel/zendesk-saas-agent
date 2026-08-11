"""Zendesk Agent v2 customer-delivery lifecycle metrics.

每个 emit 函数发一条 structlog event，统一以 ``metric.`` 前缀，方便 SigNoz
按 event name 聚合 + 出图。

2 个核心指标：

1. **agent_public_reply_sent** — v2 成功发出一条公共回复
   - 用作其它 rate 类指标的分母
   - 触发：可靠投递边界首次确认送达之后
   - **不**包含 footer 的字符数（footer 是固定加上去的）

2. **customer_reopen** — 工单已 processed 后客户又回来发消息
   - 反映"agent 自以为解决了，但客户其实没"
   - 触发：v2 intake 发现 processed 后新增客户消息
   - externally_owned / owned 状态不算 reopen
"""

from __future__ import annotations

from typing import Optional

from zd_agent.log import get_logger

logger = get_logger(module="observability.metrics")


def emit_agent_public_reply_sent(
    *,
    ticket_id: int | str,
    reply_type: str,
    template_name: str = "",
    body_chars: int = 0,
) -> None:
    """Agent 成功发出一条 v2 公共回复。

    Args:
        ticket_id: 工单 id
        reply_type: ``ask`` / ``solution`` / ``closed``
        template_name: optional writer/template identifier
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
            if hours_since_processed is not None
            else None
        ),
    )
