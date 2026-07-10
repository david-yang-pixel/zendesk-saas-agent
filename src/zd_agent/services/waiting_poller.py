"""Waiting Poller: 60s scan to discover new customer messages on messaging
tickets that Zendesk's Tickets webhook misses, plus drive timeouts on
``waiting_for_issue`` state.

Why this exists
---------------
Zendesk's native_messaging follow-up customer messages don't always create a
new ticket audit / comment, so the Tickets webhook trigger may not fire.
Without this poller, tickets in ``waiting_for_issue`` state are stuck forever.

Loop per ticket in (in_silence_window | waiting_for_issue)
-----------------------------------------------------------
1. Re-fetch ticket + conversation_log
2. Status / assignee guard:
   - ticket.status NOT IN {new, open}            → human/auto closed it, skip
   - assignee_id is real human assignee         → human/owner took over, mark
                                                   ``human_handled``-equivalent
                                                   (we use ``abandoned`` as a
                                                   terminal state for now to
                                                   stop scanning)
3. Diff customer_msgs:
   - latest customer comment_id != state's       → push
                                                   ``{source: "poller_new_msg"}``
                                                   to trigger_queue → re-judge
4. Timeouts (only if no new message and state == waiting_for_issue):
   - 24h since first_seen → state = abandoned + Feishu alert (one-shot)
   - 30min since first_seen + last_verdict == greeting → Feishu alert (one-shot)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from zd_agent.config import get_config
from zd_agent.integrations.notification_service import push_notification_card
from zd_agent.log import get_logger
from zd_agent.services.assignee import is_human_assignee
from zd_agent.services.trigger_state_machine import (
    apply_waiting_poller_abandoned,
    apply_waiting_poller_timeouts,
)
from zd_agent.services.ticket_tags import AGENT_ASKED_TAG
from zd_agent.trigger.state_store import TicketState, TicketStateTracker

logger = get_logger(module="services.waiting_poller")

_TARGET_STATES = ("in_silence_window", "waiting_for_issue")
# 默认只关注新建状态的工单。一旦 Zendesk status 变成 open/pending/solved
# poller 立刻让位停扫；唯一例外是带 AGENT_ASKED_TAG 的工单——表示 agent
# 主动给客户发了 public ask 导致 status 自动翻 open，此时仍需继续追踪
# 客户是否回应。
_OPEN_STATUSES = ("new",)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _alert_feishu(*, title: str, message: str, urgent: bool = False) -> None:
    """Best-effort Feishu alert. Failures are swallowed (just logged)."""
    try:
        payload = await push_notification_card(
            card_type="alert",
            ticket_id="",
            title=title,
            content=message,
            urgent=urgent,
            cfg=get_config(),
        )
        if not payload.get("success"):
            logger.warning(
                "feishu_alert_failed",
                title=title,
                error=str(payload.get("error") or "unknown error"),
                code=payload.get("code"),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("feishu_alert_failed", title=title, error=str(e))


async def _process_one(
    *,
    ticket_id: int,
    rec: TicketState,
    state: TicketStateTracker,
    trigger_queue: asyncio.Queue,
    abandoned_hours: int,
    greeting_alert_minutes: int,
) -> None:
    # Lazy import to avoid circular import (saas_app imports this module).
    from zd_agent.services.ticket_messages import _fetch_customer_messages

    msgs, ticket = await _fetch_customer_messages(ticket_id, log_success=False)
    if ticket is None:
        # Fetch failed — just leave it for the next round.
        return

    status = ticket.get("status", "")
    tags = ticket.get("tags") or []
    assignee_id = ticket.get("assignee_id")
    cfg_zd = get_config().zendesk
    my_uid = cfg_zd.agent_user_id or 0

    # Guard 1: ticket no longer in scope.
    # 默认只看 status=new。但 agent 主动 ask 客户后 status 会自动翻 open —
    # 我们之前给工单打了 AGENT_ASKED_TAG 表示这种情况，这种 open 仍要继续扫。
    in_scope = status in _OPEN_STATUSES or (
        status == "open" and AGENT_ASKED_TAG in tags
    )
    if not in_scope:
        transition = apply_waiting_poller_abandoned(
            state,
            ticket_id,
            reason="status_out_of_scope",
        )
        if transition is not None:
            logger.info(
                "waiting_poller_status_out_of_scope",
                ticket_id=ticket_id, status=status, prev_state=transition.prev_state,
                has_asked_tag=AGENT_ASKED_TAG in tags,
            )
        return

    # Guard 2: already has a real human assignee → don't intervene.
    if is_human_assignee(
        assignee_id,
        agent_user_id=my_uid,
        non_human_assignee_ids=cfg_zd.non_human_assignee_ids,
    ):
        transition = apply_waiting_poller_abandoned(
            state,
            ticket_id,
            reason="human_assignee",
        )
        if transition is not None:
            logger.info(
                "waiting_poller_human_assignee",
                ticket_id=ticket_id, assignee_id=assignee_id, my_uid=my_uid,
                prev_state=transition.prev_state,
            )
        return

    # Step 3: did the customer send a new message since last evaluation?
    #
    # 2026-05: messaging webhook（messaging_ticket.message_added）已经接管这一职
    # 责——客户每条消息都会通过 webhook 立即推 trigger_queue。这里的 diff 检测改
    # 为低频"兜底"模式（每 5min 才扫一次，见 run_waiting_poller scan_interval）：
    # 仅当 webhook 真的丢了某条消息时才会跑这个分支，正常情况 latest_cid 应该跟
    # rec.last_customer_comment_id 一致。
    #
    # 完全删掉这段代码也行（webhook 100% 可靠时），但保留作为 belt-and-suspenders。
    # poll_new_messages flag 控制是否启用：默认 True（兜底），将来对 webhook 信
    # 心更足后可以关掉。
    cfg = get_config()
    if cfg.waiting_poller.poll_new_messages and msgs:
        latest_cid = msgs[-1].get("id")
        if latest_cid and latest_cid != rec.last_customer_comment_id:
            logger.info(
                "waiting_poller_new_customer_msg_fallback",
                ticket_id=ticket_id,
                from_cid=rec.last_customer_comment_id, to_cid=latest_cid,
                note="webhook 漏了，poller 兜底",
            )
            await trigger_queue.put({
                "source": "poller_new_msg",
                "ticket_id": ticket_id,
                "_queue_key": f"poller_new_msg:{ticket_id}:{latest_cid}",
            })
            return

    # Step 4: no new message — apply timeouts only on waiting_for_issue.
    transition = apply_waiting_poller_timeouts(
        state,
        ticket_id,
        abandoned_hours=abandoned_hours,
        greeting_alert_minutes=greeting_alert_minutes,
        now=_now(),
    )
    if transition is None:
        return

    if transition.kind == "abandoned":
        if transition.should_alert:
            await _alert_feishu(
                title=f"Ticket #{ticket_id} 已 24h 无客户回应",
                message=(
                    f"工单 #{ticket_id} 自创建后 {abandoned_hours} 小时内"
                    f"客户未发送任何 actionable 信息（last_verdict={transition.last_verdict}）。"
                    "已标记为 abandoned，agent 不再处理。需要人工 follow-up。"
                ),
                urgent=False,
            )
        logger.info(
            "waiting_poller_abandoned",
            ticket_id=ticket_id,
            age_hours=round(transition.age_seconds / 3600, 1),
        )
        # M8 metric: 首次进入 abandoned 才埋点（避免重复扫描产生噪声）
        if transition.prev_state != "abandoned":
            try:
                from zd_agent.observability.metrics import emit_abandoned
                emit_abandoned(
                    ticket_id=ticket_id,
                    age_hours=transition.age_seconds / 3600,
                    last_verdict=transition.last_verdict,
                    ask_count=transition.ask_count,
                )
            except Exception:
                pass
        return

    if transition.kind == "greeting_alert":
        await _alert_feishu(
            title=f"Ticket #{ticket_id} 30min 仅打招呼无问题",
            message=(
                f"工单 #{ticket_id} 自创建后 {greeting_alert_minutes} 分钟"
                "客户只发了寒暄消息，未陈述具体问题。可以人工去看一眼。"
            ),
            urgent=False,
        )
        logger.info(
            "waiting_poller_greeting_alert",
            ticket_id=ticket_id,
            age_min=round(transition.age_seconds / 60, 1),
        )


async def scan_once(
    *,
    state: TicketStateTracker,
    trigger_queue: asyncio.Queue,
    abandoned_hours: int,
    greeting_alert_minutes: int,
) -> int:
    """Single scan pass over target states. Returns number of tickets processed."""
    candidates: list[str] = []
    for s in _TARGET_STATES:
        candidates.extend(state.list_by_state(s))
    processed = 0
    for tid_str in candidates:
        rec = state.get(tid_str)
        if rec is None:
            continue
        try:
            tid = int(tid_str)
        except ValueError:
            logger.warning("waiting_poller_bad_ticket_id", ticket_id=tid_str)
            continue
        try:
            await _process_one(
                ticket_id=tid, rec=rec, state=state,
                trigger_queue=trigger_queue,
                abandoned_hours=abandoned_hours,
                greeting_alert_minutes=greeting_alert_minutes,
            )
            processed += 1
        except Exception as e:
            logger.warning(
                "waiting_poller_one_error",
                ticket_id=tid, error=str(e),
            )
    return processed


async def run_waiting_poller(
    *,
    state: TicketStateTracker,
    trigger_queue: asyncio.Queue,
    scan_interval_seconds: int,
    abandoned_hours: int,
    greeting_alert_minutes: int,
) -> None:
    """Infinite loop: scan_once → sleep → repeat. Catches per-iter exceptions."""
    logger.info(
        "waiting_poller_started",
        interval_s=scan_interval_seconds,
        abandoned_h=abandoned_hours,
        greeting_alert_min=greeting_alert_minutes,
    )
    while True:
        try:
            await scan_once(
                state=state, trigger_queue=trigger_queue,
                abandoned_hours=abandoned_hours,
                greeting_alert_minutes=greeting_alert_minutes,
            )
        except Exception as e:
            logger.error("waiting_poller_error", error=str(e))
        await asyncio.sleep(scan_interval_seconds)
