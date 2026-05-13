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
   - assignee_id is set AND != my_uid            → human took over, mark
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
from zd_agent.log import get_logger
from zd_agent.trigger.state_store import TicketState, TicketStateTracker

logger = get_logger(module="entry.waiting_poller")

_TARGET_STATES = ("in_silence_window", "waiting_for_issue")
# 默认只关注新建状态的工单。一旦 Zendesk status 变成 open/pending/solved
# poller 立刻让位停扫；唯一例外是带 AGENT_ASKED_TAG 的工单——表示 agent
# 主动给客户发了 public ask 导致 status 自动翻 open，此时仍需继续追踪
# 客户是否回应。
_OPEN_STATUSES = ("new",)
# agent 发主动 public ask 后给工单打这个 tag，扩展 poller 作用域
AGENT_ASKED_TAG = "zd-agent-asked"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _alert_feishu(*, title: str, message: str, urgent: bool = False) -> None:
    """Best-effort Feishu alert. Failures are swallowed (just logged)."""
    cfg = get_config().feishu
    if not cfg.webhook_url:
        logger.warning("feishu_alert_skipped_no_url", title=title)
        return
    try:
        from zd_agent.integrations.feishu.client import FeishuClient
        client = FeishuClient(
            webhook_url=cfg.webhook_url, webhook_secret=cfg.webhook_secret,
        )
        await client.send_alert(title=title, message=message, urgent=urgent)
    except Exception as e:
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
    from zd_agent.entry.saas_app import _fetch_customer_messages

    msgs, ticket = await _fetch_customer_messages(ticket_id)
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
        if rec.state != "abandoned":
            logger.info(
                "waiting_poller_status_out_of_scope",
                ticket_id=ticket_id, status=status, prev_state=rec.state,
                has_asked_tag=AGENT_ASKED_TAG in tags,
            )
            rec.state = "abandoned"
            state.set(ticket_id, rec)
        return

    # Guard 2: assigned to a human (anyone other than us) → don't intervene
    if assignee_id and my_uid and int(assignee_id) != int(my_uid):
        if rec.state != "abandoned":
            logger.info(
                "waiting_poller_human_assignee",
                ticket_id=ticket_id, assignee_id=assignee_id, my_uid=my_uid,
                prev_state=rec.state,
            )
            rec.state = "abandoned"
            state.set(ticket_id, rec)
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
            })
            return

    # Step 4: no new message — apply timeouts only on waiting_for_issue
    if rec.state != "waiting_for_issue":
        return

    now = _now()
    if rec.first_seen.tzinfo is None:
        first_seen = rec.first_seen.replace(tzinfo=timezone.utc)
    else:
        first_seen = rec.first_seen
    age_seconds = (now - first_seen).total_seconds()

    # 24h cap → abandoned + alert
    if age_seconds >= abandoned_hours * 3600:
        first_abandon = rec.state != "abandoned"
        if rec.abandoned_alert_sent_at is None:
            await _alert_feishu(
                title=f"Ticket #{ticket_id} 已 24h 无客户回应",
                message=(
                    f"工单 #{ticket_id} 自创建后 {abandoned_hours} 小时内"
                    f"客户未发送任何 actionable 信息（last_verdict={rec.last_verdict}）。"
                    "已标记为 abandoned，agent 不再处理。需要人工 follow-up。"
                ),
                urgent=False,
            )
            rec.abandoned_alert_sent_at = now
        rec.state = "abandoned"
        state.set(ticket_id, rec)
        logger.info(
            "waiting_poller_abandoned",
            ticket_id=ticket_id, age_hours=round(age_seconds / 3600, 1),
        )
        # M8 metric: 首次进入 abandoned 才埋点（避免重复扫描产生噪声）
        if first_abandon:
            try:
                from zd_agent.observability.metrics import emit_abandoned
                emit_abandoned(
                    ticket_id=ticket_id,
                    age_hours=age_seconds / 3600,
                    last_verdict=rec.last_verdict or "",
                    ask_count=rec.ask_count,
                )
            except Exception:
                pass
        return

    # 30min greeting alert (one-shot)
    if (
        rec.last_verdict == "greeting"
        and age_seconds >= greeting_alert_minutes * 60
        and rec.alert_sent_at is None
    ):
        await _alert_feishu(
            title=f"Ticket #{ticket_id} 30min 仅打招呼无问题",
            message=(
                f"工单 #{ticket_id} 自创建后 {greeting_alert_minutes} 分钟"
                "客户只发了寒暄消息，未陈述具体问题。可以人工去看一眼。"
            ),
            urgent=False,
        )
        rec.alert_sent_at = now
        state.set(ticket_id, rec)
        logger.info(
            "waiting_poller_greeting_alert",
            ticket_id=ticket_id, age_min=round(age_seconds / 60, 1),
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
