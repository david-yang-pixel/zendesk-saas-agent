"""Tests verifying the M8 metrics are wired into the right call sites.

Wiring map:
- ``set_escalated_to_human_action(reason="customer_requested")`` → emit_customer_requested_human
- ``set_escalated_to_human_action(reason="high_risk_*")``        → NOT emitted (agent-initiated)
- ``set_escalated_to_human_action(reason="max_asks_reached")``   → NOT emitted (agent-initiated)
- ``send_public_reply()`` 成功 → emit_agent_public_reply_sent
- ``run_saas_trigger_consumer`` 见 state==processed + 新 cid → emit_customer_reopen
- ``waiting_poller`` 首次进 abandoned → emit_abandoned
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, FeishuConfig, PublicReplyConfig, ZendeskConfig
from zd_agent.services.trigger_actions import set_escalated_to_human_action
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now():
    return datetime.now(timezone.utc)


def _make_cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=False)
    cfg.feishu = FeishuConfig(webhook_url="", webhook_secret="")
    cfg.zendesk = ZendeskConfig(
        subdomain="test", email="x@y.com", api_token="t",
    )
    return cfg


async def _call_escalate(
    *, state, tmp_path, reason: str, ticket_id: int = 9999,
    existing: TicketState | None = None,
    customer_phrase: str = "",
) -> None:
    log_path = Path(tmp_path) / "trigger.jsonl"
    now = _now()
    await set_escalated_to_human_action(
        ticket_id=ticket_id, reason=reason,
        existing=existing, state=state, log_path=log_path,
        source="zendesk_webhook", now=now,
        verdict_str="request_human" if reason == "customer_requested" else "substantial_issue",
        latest_cid="cid-1", latest_ts=now,
        completeness=100,
        customer_msgs=[],
        customer_phrase=customer_phrase,
    )


# ---------------------------------------------------------------------------
# 1) customer_requested_human metric wiring
# ---------------------------------------------------------------------------

class TestCustomerRequestedHumanWiring:
    @pytest.mark.asyncio
    async def test_customer_requested_emits_metric(self, tmp_path) -> None:
        state = TicketStateTracker(tmp_path / "tickets.json")
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=3,
        )
        state.set(9999, existing)

        with (
            patch(
                "zd_agent.observability.metrics.emit_customer_requested_human",
            ) as mock_emit,
            patch("zd.client.ZendeskClient", return_value=MagicMock()),
            patch(
                "zd_agent.config.get_config", return_value=_make_cfg(),
            ),
        ):
            await _call_escalate(
                state=state, tmp_path=tmp_path,
                reason="customer_requested",
                existing=existing,
                customer_phrase="please connect me to a human",
            )

        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["ticket_id"] == 9999
        assert kwargs["source"] == "zendesk_webhook"
        assert kwargs["customer_phrase"] == "please connect me to a human"
        assert kwargs["ask_count_so_far"] == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reason", [
        "high_risk_refund",
        "high_risk_emotional",
        "high_risk_incident",
        "max_asks_reached",
    ])
    async def test_agent_initiated_escalate_no_metric(
        self, tmp_path, reason: str,
    ) -> None:
        """agent 主动放弃（高危 / 上限）不算客户拒绝，不该 emit"""
        state = TicketStateTracker(tmp_path / "tickets.json")
        with (
            patch(
                "zd_agent.observability.metrics.emit_customer_requested_human",
            ) as mock_emit,
            patch("zd.client.ZendeskClient", return_value=MagicMock()),
            patch(
                "zd_agent.config.get_config", return_value=_make_cfg(),
            ),
        ):
            await _call_escalate(
                state=state, tmp_path=tmp_path, reason=reason,
            )
        mock_emit.assert_not_called()


# ---------------------------------------------------------------------------
# 2) agent_public_reply_sent metric wiring
# ---------------------------------------------------------------------------

class TestAgentPublicReplySentWiring:
    @pytest.mark.asyncio
    async def test_send_public_reply_success_emits_metric(self) -> None:
        """send_public_reply() relay 路径成功 → emit metric"""
        from zd_agent.tools.zendesk_public_reply import send_public_reply

        cfg = _make_cfg()
        cfg.public_reply = PublicReplyConfig(enabled=True)
        cfg.public_reply.browser_relay_url = "http://127.0.0.1:8097"
        cfg.public_reply.browser_relay_api_key = "test-relay-key"

        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.get_config",
                return_value=cfg,
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
                new=AsyncMock(),
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
                new=AsyncMock(return_value={
                    "success": True,
                    "data": {"comments": []},
                }),
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply._check_freshness_before_relay",
                new=AsyncMock(return_value=MagicMock(continue_send=True)),
            ),
            patch(
                "zd_agent.observability.metrics.emit_agent_public_reply_sent",
            ) as mock_emit,
        ):
            success, _ = await send_public_reply(
                ticket_id=12345,
                body="Hi! Could you describe the issue?",
                reply_type="ask",
                conversation_id="conv-1",
                snapshot_customer_message_count=0,
            )

        assert success is True
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["ticket_id"] == 12345
        assert kwargs["reply_type"] == "ask"
        # body_chars 应是不含 footer 的字符数
        assert kwargs["body_chars"] == len("Hi! Could you describe the issue?")

    @pytest.mark.asyncio
    async def test_send_public_reply_kill_switch_no_metric(self) -> None:
        """kill switch OFF → 直接返回 False，不该 emit metric"""
        from zd_agent.tools.zendesk_public_reply import send_public_reply

        cfg = _make_cfg()  # enabled=False

        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.get_config",
                return_value=cfg,
            ),
            patch(
                "zd_agent.observability.metrics.emit_agent_public_reply_sent",
            ) as mock_emit,
        ):
            success, _ = await send_public_reply(
                ticket_id=1, body="hi", reply_type="ask",
            )

        assert success is False
        mock_emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_metric_failure_does_not_break_send(self) -> None:
        """metric emit 抛 exception → 发送主流程仍 return success"""
        from zd_agent.tools.zendesk_public_reply import send_public_reply

        cfg = _make_cfg()
        cfg.public_reply = PublicReplyConfig(enabled=True)
        cfg.public_reply.browser_relay_url = "http://127.0.0.1:8097"
        cfg.public_reply.browser_relay_api_key = "test-relay-key"

        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.get_config",
                return_value=cfg,
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
                new=AsyncMock(),
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
                new=AsyncMock(return_value={
                    "success": True,
                    "data": {"comments": []},
                }),
            ),
            patch(
                "zd_agent.tools.zendesk_public_reply._check_freshness_before_relay",
                new=AsyncMock(return_value=MagicMock(continue_send=True)),
            ),
            patch(
                "zd_agent.observability.metrics.emit_agent_public_reply_sent",
                side_effect=RuntimeError("logger broken"),
            ),
        ):
            success, _ = await send_public_reply(
                ticket_id=1,
                body="hi",
                reply_type="ask",
                conversation_id="conv-1",
                snapshot_customer_message_count=0,
            )

        # 主流程仍成功
        assert success is True


# ---------------------------------------------------------------------------
# 3) abandoned metric wiring
# ---------------------------------------------------------------------------

class TestAbandonedWiring:
    @pytest.mark.asyncio
    async def test_first_abandon_emits_metric(self, tmp_path) -> None:
        """waiting_poller 把 state 从 waiting_for_issue 转到 abandoned → emit"""
        import asyncio

        from zd_agent.services.waiting_poller import _process_one

        state = TicketStateTracker(tmp_path / "tickets.json")
        # first_seen 25 小时前 → 触发 abandoned
        old_first_seen = _now() - timedelta(hours=25)
        rec = TicketState(
            state="waiting_for_issue",
            first_seen=old_first_seen,
            last_verdict="greeting",
            ask_count=2,
        )
        state.set(9999, rec)

        trigger_queue: asyncio.Queue = asyncio.Queue()

        # Mock ticket fetcher to return in-scope ticket
        with (
            patch(
                "zd_agent.services.ticket_messages._fetch_customer_messages",
                AsyncMock(return_value=(
                    [],
                    {"id": 9999, "status": "new", "tags": [], "assignee_id": None},
                )),
            ),
            patch(
                "zd_agent.services.waiting_poller._alert_feishu",
                AsyncMock(),
            ),
            patch(
                "zd_agent.observability.metrics.emit_abandoned",
            ) as mock_emit,
            patch(
                "zd_agent.services.waiting_poller.get_config",
                return_value=_make_cfg(),
            ),
        ):
            await _process_one(
                ticket_id=9999, rec=rec, state=state,
                trigger_queue=trigger_queue,
                abandoned_hours=24,
                greeting_alert_minutes=30,
            )

        # state 转 abandoned
        assert state.get(9999).state == "abandoned"
        # metric 被 emit 一次
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["ticket_id"] == 9999
        assert kwargs["age_hours"] >= 24
        assert kwargs["last_verdict"] == "greeting"
        assert kwargs["ask_count"] == 2

    @pytest.mark.asyncio
    async def test_already_abandoned_no_duplicate_metric(self, tmp_path) -> None:
        """state 已经是 abandoned → 不再 emit（防重复扫描刷指标）"""
        import asyncio

        from zd_agent.services.waiting_poller import _process_one

        state = TicketStateTracker(tmp_path / "tickets.json")
        old_first_seen = _now() - timedelta(hours=25)
        rec = TicketState(
            state="abandoned",
            first_seen=old_first_seen,
            abandoned_alert_sent_at=_now(),
        )
        state.set(9999, rec)
        trigger_queue: asyncio.Queue = asyncio.Queue()

        with (
            patch(
                "zd_agent.services.ticket_messages._fetch_customer_messages",
                AsyncMock(return_value=(
                    [],
                    {"id": 9999, "status": "new", "tags": [], "assignee_id": None},
                )),
            ),
            patch(
                "zd_agent.services.waiting_poller._alert_feishu",
                AsyncMock(),
            ),
            patch(
                "zd_agent.observability.metrics.emit_abandoned",
            ) as mock_emit,
            patch(
                "zd_agent.services.waiting_poller.get_config",
                return_value=_make_cfg(),
            ),
        ):
            await _process_one(
                ticket_id=9999, rec=rec, state=state,
                trigger_queue=trigger_queue,
                abandoned_hours=24,
                greeting_alert_minutes=30,
            )

        mock_emit.assert_not_called()
