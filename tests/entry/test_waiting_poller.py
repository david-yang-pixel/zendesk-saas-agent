"""Test waiting_poller scan logic + timeout transitions."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.entry.waiting_poller import scan_once, _process_one
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def state(tmp_path):
    return TicketStateTracker(tmp_path / "tickets.json")


@pytest.fixture
def queue():
    return asyncio.Queue()


def _ticket(status="new", assignee_id=None, ticket_id=100, tags=None):
    return {
        "id": ticket_id,
        "status": status,
        "assignee_id": assignee_id,
        "tags": tags or [],
        "via": {"channel": "native_messaging"},
    }


@pytest.mark.asyncio
async def test_new_customer_msg_pushes_queue(state, queue):
    """state.last_customer_comment_id 与最新 cid 不一致 → 推 queue 走重判。"""
    state.set("1", TicketState(
        state="waiting_for_issue", first_seen=_now() - timedelta(minutes=10),
        last_customer_comment_id="OLD_CID", last_verdict="insufficient_info",
    ))
    new_msgs = [{"id": "NEW_CID", "body": "actually here is the bug"}]

    async def fake_fetch(_tid):
        return new_msgs, _ticket()

    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(
            state=state, trigger_queue=queue,
            abandoned_hours=24, greeting_alert_minutes=30,
        )
    msg = queue.get_nowait()
    assert msg == {"source": "poller_new_msg", "ticket_id": 1}


@pytest.mark.asyncio
async def test_no_new_msg_within_window_does_nothing(state, queue):
    state.set("1", TicketState(
        state="waiting_for_issue",
        first_seen=_now() - timedelta(minutes=5),
        last_customer_comment_id="C1", last_verdict="insufficient_info",
    ))
    msgs = [{"id": "C1", "body": "..."}]
    async def fake_fetch(_tid):
        return msgs, _ticket()

    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert queue.empty()
    assert state.get("1").state == "waiting_for_issue"
    assert state.get("1").alert_sent_at is None


@pytest.mark.asyncio
async def test_status_pending_marks_abandoned(state, queue):
    """ticket.status=pending → 切 state=abandoned，停扫。"""
    state.set("1", TicketState(
        state="waiting_for_issue", first_seen=_now(),
        last_customer_comment_id="C1",
    ))
    msgs = [{"id": "C1", "body": "x"}]
    async def fake_fetch(_tid):
        return msgs, _ticket(status="pending")
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert state.get("1").state == "abandoned"


@pytest.mark.asyncio
async def test_status_open_without_asked_tag_marks_abandoned(state, queue):
    """status=open 且没有 zd-agent-asked tag → 让位 abandoned。"""
    state.set("1", TicketState(
        state="waiting_for_issue", first_seen=_now(),
        last_customer_comment_id="C1",
    ))
    msgs = [{"id": "C1", "body": "x"}]
    async def fake_fetch(_tid):
        return msgs, _ticket(status="open")
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert state.get("1").state == "abandoned"


@pytest.mark.asyncio
async def test_status_open_with_asked_tag_keeps_scanning(state, queue):
    """agent 主动 ask 客户后 status=open + zd-agent-asked tag → 仍然继续扫。

    场景：3min silence 到期 → public ask → status auto-flip new→open。
    poller 应该认 tag 例外，继续追踪客户是否回应。
    """
    from zd_agent.entry.waiting_poller import AGENT_ASKED_TAG

    state.set("1", TicketState(
        state="waiting_for_issue", first_seen=_now() - timedelta(minutes=2),
        last_customer_comment_id="OLD",
        nudge_sent_at=_now() - timedelta(minutes=1),
    ))
    msgs = [{"id": "NEW", "body": "now I have the error message"}]
    async def fake_fetch(_tid):
        return msgs, _ticket(status="open", tags=[AGENT_ASKED_TAG])
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    # 状态没变（还在 waiting_for_issue），且因为客户发新消息推了 queue
    assert state.get("1").state == "waiting_for_issue"
    msg = queue.get_nowait()
    assert msg["source"] == "poller_new_msg"
    assert msg["ticket_id"] == 1


@pytest.mark.asyncio
async def test_status_pending_with_asked_tag_still_marks_abandoned(state, queue):
    """tag 例外仅对 status=open 有效；pending/solved 仍让位。"""
    from zd_agent.entry.waiting_poller import AGENT_ASKED_TAG

    state.set("1", TicketState(
        state="waiting_for_issue", first_seen=_now(),
        last_customer_comment_id="C1",
    ))
    msgs = [{"id": "C1", "body": "x"}]
    async def fake_fetch(_tid):
        return msgs, _ticket(status="pending", tags=[AGENT_ASKED_TAG])
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert state.get("1").state == "abandoned"


@pytest.mark.asyncio
async def test_human_assignee_marks_abandoned(state, queue):
    """assignee 不是 yonggang → 切 abandoned，停扫。"""
    state.set("1", TicketState(
        state="waiting_for_issue", first_seen=_now(),
        last_customer_comment_id="C1",
    ))
    msgs = [{"id": "C1", "body": "x"}]
    async def fake_fetch(_tid):
        return msgs, _ticket(assignee_id=99999)  # someone else
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert state.get("1").state == "abandoned"


@pytest.mark.asyncio
async def test_my_assignee_marks_abandoned(state, queue):
    """只看是否有受托人：assignee 是 yonggang 也让位。"""
    state.set("1", TicketState(
        state="waiting_for_issue", first_seen=_now(),
        last_customer_comment_id="C1",
    ))
    msgs = [{"id": "C1", "body": "x"}]
    async def fake_fetch(_tid):
        return msgs, _ticket(assignee_id=45433304089364)
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert state.get("1").state == "abandoned"


@pytest.mark.asyncio
async def test_automation_assignee_does_not_mark_abandoned(state, queue):
    """AI Agent / SaaS 自动身份不算人工受托人，继续追踪。"""
    state.set("1", TicketState(
        state="waiting_for_issue", first_seen=_now(),
        last_customer_comment_id="C1",
    ))
    msgs = [{"id": "C1", "body": "x"}]

    async def fake_fetch(_tid):
        return msgs, _ticket(assignee_id=46493567999892)

    from zd_agent.config import AppConfig, ZendeskConfig

    cfg = AppConfig()
    cfg.zendesk = ZendeskConfig(
        agent_user_id=45433304089364,
        non_human_assignee_ids=[46493567999892],
    )

    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch), \
         patch("zd_agent.entry.waiting_poller.get_config", return_value=cfg):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)

    assert state.get("1").state == "waiting_for_issue"


@pytest.mark.asyncio
async def test_24h_timeout_marks_abandoned_and_alerts(state, queue):
    """first_seen 25h ago → state=abandoned + 飞书告警一次。"""
    state.set("1", TicketState(
        state="waiting_for_issue",
        first_seen=_now() - timedelta(hours=25),
        last_customer_comment_id="C1", last_verdict="insufficient_info",
    ))
    msgs = [{"id": "C1", "body": "x"}]
    async def fake_fetch(_tid):
        return msgs, _ticket()
    sent_alerts: list[dict] = []
    async def fake_alert(*, title, message, urgent=False):
        sent_alerts.append({"title": title, "message": message})
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch), \
         patch("zd_agent.entry.waiting_poller._alert_feishu",
               side_effect=fake_alert):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    rec = state.get("1")
    assert rec.state == "abandoned"
    assert rec.abandoned_alert_sent_at is not None
    assert len(sent_alerts) == 1
    assert "24h" in sent_alerts[0]["title"]


@pytest.mark.asyncio
async def test_24h_timeout_does_not_double_alert(state, queue):
    """abandoned_alert_sent_at 已设 → 不再发第二次告警。"""
    state.set("1", TicketState(
        state="waiting_for_issue",
        first_seen=_now() - timedelta(hours=25),
        last_customer_comment_id="C1", last_verdict="insufficient_info",
        abandoned_alert_sent_at=_now() - timedelta(minutes=5),
    ))
    msgs = [{"id": "C1", "body": "x"}]
    async def fake_fetch(_tid):
        return msgs, _ticket()
    sent_alerts: list[dict] = []
    async def fake_alert(*, title, message, urgent=False):
        sent_alerts.append({"title": title})
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch), \
         patch("zd_agent.entry.waiting_poller._alert_feishu",
               side_effect=fake_alert):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert sent_alerts == []  # 不重复发


@pytest.mark.asyncio
async def test_30min_greeting_alert_one_shot(state, queue):
    """last_verdict=greeting + 31min → 飞书告警 alert_sent_at 设。重扫不重发。"""
    state.set("1", TicketState(
        state="waiting_for_issue",
        first_seen=_now() - timedelta(minutes=31),
        last_customer_comment_id="C1", last_verdict="greeting",
    ))
    msgs = [{"id": "C1", "body": "hi"}]
    async def fake_fetch(_tid):
        return msgs, _ticket()
    sent_alerts: list[dict] = []
    async def fake_alert(*, title, message, urgent=False):
        sent_alerts.append({"title": title})

    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch), \
         patch("zd_agent.entry.waiting_poller._alert_feishu",
               side_effect=fake_alert):
        # 第一轮：发告警
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
        # 第二轮：state.alert_sent_at 已设，不重发
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert len(sent_alerts) == 1
    assert state.get("1").alert_sent_at is not None


@pytest.mark.asyncio
async def test_in_silence_window_state_also_scanned_for_new_msg(state, queue):
    """state=in_silence_window 也应被 poller 扫到（用于追新消息）。"""
    state.set("1", TicketState(
        state="in_silence_window",
        first_seen=_now() - timedelta(minutes=2),
        last_customer_comment_id="OLD",
        silence_window_end=_now() + timedelta(minutes=2),
    ))
    msgs = [{"id": "NEW", "body": "follow-up"}]
    async def fake_fetch(_tid):
        return msgs, _ticket()
    with patch("zd_agent.entry.saas_app._fetch_customer_messages",
               side_effect=fake_fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    msg = queue.get_nowait()
    assert msg["source"] == "poller_new_msg"


@pytest.mark.asyncio
async def test_processed_state_not_scanned(state, queue):
    state.set("1", TicketState(
        state="processed",
        first_seen=_now() - timedelta(hours=25),
        last_customer_comment_id="C1",
    ))
    fetch = AsyncMock(return_value=([{"id": "OTHER"}], _ticket()))
    with patch("zd_agent.entry.saas_app._fetch_customer_messages", fetch):
        await scan_once(state=state, trigger_queue=queue,
                        abandoned_hours=24, greeting_alert_minutes=30)
    assert fetch.await_count == 0  # 根本不该被 fetch
    assert queue.empty()
