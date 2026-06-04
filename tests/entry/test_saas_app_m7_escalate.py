"""Tests for M7 escalate side effects + 4-entry trigger skip.

M7 在 _set_escalated_to_human 加了 3 个对外动作（best-effort，互相隔离）：
1. 发 public handoff 模板 E 给客户（kill switch 受 send_public_reply 内部门控）
2. 飞书推送 ticket_escalate 卡片（cfg.feishu.webhook_url 空时跳过）
3. Zendesk 工单加 tag escalated-to-human（best-effort）

外加 trigger_consumer 队列消费 loop 顶部：
- state.state == "escalated_to_human" 一律 skip（覆盖 4 个 entry：
  mention / silence_waker / zendesk_webhook / waiting_poller）

测试覆盖：
- state 写入是强制的（哪怕全部副作用挂了 state 也得在）
- 3 个副作用相互隔离（任一抛 exception 不影响其它 + 不影响 state）
- 飞书 webhook_url 空时正确 short-circuit（不调 client）
- handoff 模板带客户名 + 不含 footer 标记（footer 工具层加）
- 4 个 source 一律 skip
- log_decision 在 skip 时被记录
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, FeishuConfig, PublicReplyConfig, ZendeskConfig
from zd_agent.entry.saas_app import _set_escalated_to_human, _trigger_consumer
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _make_state(tmp_path) -> TicketStateTracker:
    return TicketStateTracker(tmp_path / "tickets.json")


def _make_cfg(
    *,
    feishu_url: str = "",
    public_reply_enabled: bool = False,
    subdomain: str = "dify-test",
) -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=public_reply_enabled)
    cfg.feishu = FeishuConfig(webhook_url=feishu_url, webhook_secret="")
    cfg.zendesk = ZendeskConfig(
        subdomain=subdomain, email="x@y.com", api_token="t",
    )
    return cfg


async def _call_escalate(
    *,
    state: TicketStateTracker,
    tmp_path,
    ticket_id: int = 9999,
    reason: str = "customer_requested",
    customer_msgs: list[dict] | None = None,
    customer_phrase: str = "",
    source: str = "zendesk_webhook",
) -> None:
    log_path = Path(tmp_path) / "trigger.jsonl"
    now = _now()
    await _set_escalated_to_human(
        ticket_id=ticket_id,
        reason=reason,
        existing=None,
        state=state,
        log_path=log_path,
        source=source,
        now=now,
        verdict_str="request_human",
        latest_cid="cid-1",
        latest_ts=now,
        completeness=100,
        customer_msgs=customer_msgs or [],
        customer_phrase=customer_phrase,
    )


# ---------------------------------------------------------------------------
# 1) state 写入是强制的
# ---------------------------------------------------------------------------

class TestStateAlwaysWritten:
    @pytest.mark.asyncio
    async def test_state_written_when_all_side_effects_fail(self, tmp_path) -> None:
        """3 个副作用全部抛 exception，state 仍必须正确写入"""
        state = _make_state(tmp_path)
        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                AsyncMock(side_effect=RuntimeError("send err")),
            ),
            patch(
                "zd_agent.integrations.feishu.client.FeishuClient",
                side_effect=RuntimeError("feishu err"),
            ),
            patch(
                "zd.client.ZendeskClient",
                side_effect=RuntimeError("zd err"),
            ),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(
                    feishu_url="https://example.com/hook",
                    public_reply_enabled=True,
                ),
            ),
        ):
            await _call_escalate(state=state, tmp_path=tmp_path)

        rec = state.get(9999)
        assert rec is not None
        assert rec.state == "escalated_to_human"
        assert rec.escalated_reason == "customer_requested"
        assert rec.escalated_at is not None

    @pytest.mark.asyncio
    async def test_state_written_when_kill_switch_off(self, tmp_path) -> None:
        """kill switch off → send_public_reply 自然失败但 state 必须写入"""
        state = _make_state(tmp_path)
        with patch(
            "zd_agent.entry.saas_app.get_config",
            return_value=_make_cfg(public_reply_enabled=False),
        ):
            await _call_escalate(state=state, tmp_path=tmp_path)

        rec = state.get(9999)
        assert rec is not None
        assert rec.state == "escalated_to_human"


# ---------------------------------------------------------------------------
# 2) handoff 模板发送：kill switch + 客户名
# ---------------------------------------------------------------------------

class TestHandoffReply:
    @pytest.mark.asyncio
    async def test_handoff_called_with_customer_name(self, tmp_path) -> None:
        """customer_msgs 含 formResponse → handoff body 含客户名"""
        state = _make_state(tmp_path)
        customer_msgs = [
            {
                "id": "1", "author_id": 111,
                "body": (
                    "Name: Alice\n"
                    "Email: alice@example.com\n"
                    "Workspace ID: ws-xyz"
                ),
            },
        ]

        mock_send = AsyncMock(return_value=(True, "ok"))
        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                mock_send,
            ),
            patch("zd.client.ZendeskClient", return_value=MagicMock()),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(public_reply_enabled=True),
            ),
        ):
            await _call_escalate(
                state=state, tmp_path=tmp_path,
                customer_msgs=customer_msgs,
            )

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["reply_type"] == "handoff"
        assert kwargs["ticket_id"] == 9999
        # body 应含 "Alice"
        assert "Alice" in kwargs["body"]
        # body 应含 "human teammate"
        assert "human" in kwargs["body"].lower()

    @pytest.mark.asyncio
    async def test_handoff_no_customer_name(self, tmp_path) -> None:
        """没拿到 customer name → body 仍能发出来（用 Hi! 兜底）"""
        state = _make_state(tmp_path)
        mock_send = AsyncMock(return_value=(True, "ok"))
        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                mock_send,
            ),
            patch("zd.client.ZendeskClient", return_value=MagicMock()),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(public_reply_enabled=True),
            ),
        ):
            await _call_escalate(state=state, tmp_path=tmp_path)

        mock_send.assert_called_once()
        body = mock_send.call_args.kwargs["body"]
        # 没奇怪的空格
        assert "Hi ," not in body
        assert "Hi  " not in body

    @pytest.mark.asyncio
    async def test_human_takeover_detected_does_not_send_handoff(
        self, tmp_path,
    ) -> None:
        """已有受托人/人工接手时，只让位，不再给客户发 handoff。"""
        state = _make_state(tmp_path)
        mock_send = AsyncMock(return_value=(True, "ok"))
        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                mock_send,
            ),
            patch("zd.client.ZendeskClient", return_value=MagicMock()),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(public_reply_enabled=True),
            ),
        ):
            await _call_escalate(
                state=state,
                tmp_path=tmp_path,
                reason="human_takeover_detected",
            )

        assert state.get(9999).state == "escalated_to_human"
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_handoff_send_exception_isolated(self, tmp_path) -> None:
        """send_public_reply 抛 exception → 飞书 + tag 仍然执行 + state 写入"""
        state = _make_state(tmp_path)
        feishu_instance = MagicMock()
        feishu_instance.send_ticket_escalate = AsyncMock()
        zd_instance = MagicMock()

        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                AsyncMock(side_effect=RuntimeError("send err")),
            ),
            patch(
                "zd_agent.integrations.feishu.client.FeishuClient",
                return_value=feishu_instance,
            ),
            patch("zd.client.ZendeskClient", return_value=zd_instance),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(
                    feishu_url="https://example.com/hook",
                    public_reply_enabled=True,
                ),
            ),
        ):
            await _call_escalate(state=state, tmp_path=tmp_path)

        # state 写入
        assert state.get(9999).state == "escalated_to_human"
        # 飞书仍被调用
        feishu_instance.send_ticket_escalate.assert_called_once()
        # tag 仍被调用
        zd_instance.add_ticket_tags.assert_called_once()


# ---------------------------------------------------------------------------
# 3) 飞书推送
# ---------------------------------------------------------------------------

class TestFeishuEscalate:
    @pytest.mark.asyncio
    async def test_feishu_skipped_when_webhook_empty(self, tmp_path) -> None:
        """cfg.feishu.webhook_url 空 → 不构造 FeishuClient"""
        state = _make_state(tmp_path)
        mock_feishu_cls = MagicMock()

        with (
            patch(
                "zd_agent.integrations.feishu.client.FeishuClient",
                mock_feishu_cls,
            ),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(feishu_url=""),
            ),
        ):
            await _call_escalate(state=state, tmp_path=tmp_path)

        mock_feishu_cls.assert_not_called()
        # state 仍写入
        assert state.get(9999).state == "escalated_to_human"

    @pytest.mark.asyncio
    async def test_feishu_called_with_reason_and_phrase(self, tmp_path) -> None:
        """飞书 reason 内容含升级原因 / 客户原话 / source"""
        state = _make_state(tmp_path)
        feishu_instance = MagicMock()
        feishu_instance.send_ticket_escalate = AsyncMock()

        with (
            patch(
                "zd_agent.integrations.feishu.client.FeishuClient",
                return_value=feishu_instance,
            ),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(
                    feishu_url="https://example.com/hook",
                    subdomain="dify-test",
                ),
            ),
        ):
            await _call_escalate(
                state=state, tmp_path=tmp_path,
                reason="high_risk_refund",
                customer_phrase="我要退款 $200",
                source="zendesk_webhook",
            )

        feishu_instance.send_ticket_escalate.assert_called_once()
        kwargs = feishu_instance.send_ticket_escalate.call_args.kwargs
        assert kwargs["ticket_id"] == "9999"
        reason_text = kwargs["reason"]
        assert "high_risk_refund" in reason_text
        assert "退款" in reason_text
        assert "zendesk_webhook" in reason_text
        # zendesk_url 拼接正确
        assert "dify-test" in kwargs["zendesk_url"]
        assert "9999" in kwargs["zendesk_url"]

    @pytest.mark.asyncio
    async def test_feishu_exception_isolated(self, tmp_path) -> None:
        """飞书抛 exception → tag 仍执行 + state 仍写入"""
        state = _make_state(tmp_path)
        zd_instance = MagicMock()

        with (
            patch(
                "zd_agent.integrations.feishu.client.FeishuClient",
                side_effect=RuntimeError("feishu err"),
            ),
            patch("zd.client.ZendeskClient", return_value=zd_instance),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(
                    feishu_url="https://example.com/hook",
                ),
            ),
        ):
            await _call_escalate(state=state, tmp_path=tmp_path)

        # tag 仍被调用
        zd_instance.add_ticket_tags.assert_called_once()
        # state 仍写入
        assert state.get(9999).state == "escalated_to_human"


# ---------------------------------------------------------------------------
# 4) Zendesk tag
# ---------------------------------------------------------------------------

class TestZendeskTag:
    @pytest.mark.asyncio
    async def test_zendesk_tag_added(self, tmp_path) -> None:
        """Zendesk client.add_ticket_tags 被传入正确的参数"""
        state = _make_state(tmp_path)
        zd_instance = MagicMock()

        with (
            patch("zd.client.ZendeskClient", return_value=zd_instance),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(),
            ),
        ):
            await _call_escalate(state=state, tmp_path=tmp_path)

        zd_instance.add_ticket_tags.assert_called_once_with(
            9999, ["escalated-to-human"],
        )

    @pytest.mark.asyncio
    async def test_zendesk_tag_exception_isolated(self, tmp_path) -> None:
        """ZendeskClient 抛 exception → state 仍写入"""
        state = _make_state(tmp_path)
        with (
            patch(
                "zd.client.ZendeskClient",
                side_effect=RuntimeError("zd err"),
            ),
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(),
            ),
        ):
            await _call_escalate(state=state, tmp_path=tmp_path)

        assert state.get(9999).state == "escalated_to_human"


# ---------------------------------------------------------------------------
# 5) trigger_consumer 4-entry skip
# ---------------------------------------------------------------------------

class TestTriggerSkipEscalated:
    """已转人工的工单从 4 个 entry 入口（mention / silence_waker /
    zendesk_webhook / waiting_poller / silence_expired_ask）一律 skip。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source", [
        "mention",
        "silence_waker",
        "zendesk_webhook",
        "waiting_poller",
        "silence_expired_ask",
    ])
    async def test_trigger_skips_when_state_escalated(
        self, tmp_path, source: str,
    ) -> None:
        """trigger_consumer 顶部 skip → manager.dispatch 永远不会被调用"""
        state = _make_state(tmp_path)
        # 预先标记为 escalated
        state.set(9999, TicketState(
            state="escalated_to_human",
            first_seen=_now(),
            escalated_at=_now(),
            escalated_reason="customer_requested",
        ))

        queue: asyncio.Queue = asyncio.Queue()
        manager = MagicMock()
        manager.dispatch = AsyncMock()

        mock_log_decision = MagicMock()

        # 塞一个消息进 queue，再放一个 sentinel 让 consumer 退出
        await queue.put({"ticket_id": 9999, "source": source})

        # 用一个 cancel 的方式让 consumer loop 退出
        async def _run_once():
            try:
                await asyncio.wait_for(
                    _trigger_consumer(queue, manager, state),
                    timeout=0.3,
                )
            except asyncio.TimeoutError:
                pass

        with (
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(),
            ),
            patch(
                "zd_agent.trigger.decision_log.log_decision",
                mock_log_decision,
            ),
        ):
            await _run_once()

        # 关键：dispatch 永远没被调用（因为 skip 在最顶部）
        manager.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_skip_logs_decision(self, tmp_path) -> None:
        """skip 时 log_decision(verdict=escalated_to_human, action=skip)"""
        state = _make_state(tmp_path)
        state.set(9999, TicketState(
            state="escalated_to_human",
            first_seen=_now(),
            escalated_at=_now(),
            escalated_reason="high_risk_refund",
        ))

        queue: asyncio.Queue = asyncio.Queue()
        manager = MagicMock()
        manager.dispatch = AsyncMock()

        mock_log_decision = MagicMock()
        await queue.put({"ticket_id": 9999, "source": "zendesk_webhook"})

        with (
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(),
            ),
            patch(
                "zd_agent.trigger.decision_log.log_decision",
                mock_log_decision,
            ),
        ):
            try:
                await asyncio.wait_for(
                    _trigger_consumer(queue, manager, state),
                    timeout=0.3,
                )
            except asyncio.TimeoutError:
                pass

        # 找到一次 verdict=escalated_to_human action=skip 的记录
        skip_calls = [
            c for c in mock_log_decision.call_args_list
            if c.kwargs.get("verdict") == "escalated_to_human"
            and c.kwargs.get("action") == "skip"
        ]
        assert len(skip_calls) == 1
        extra = skip_calls[0].kwargs.get("extra") or {}
        assert extra.get("source") == "zendesk_webhook"
        assert extra.get("escalated_reason") == "high_risk_refund"

    @pytest.mark.asyncio
    async def test_trigger_not_skipped_when_not_escalated(self, tmp_path) -> None:
        """state != escalated_to_human → 走正常分支（mention 直 dispatch）"""
        state = _make_state(tmp_path)
        state.set(9999, TicketState(
            state="waiting_for_issue",
            first_seen=_now(),
            ask_count=2,
        ))

        queue: asyncio.Queue = asyncio.Queue()
        manager = MagicMock()
        manager.dispatch = AsyncMock()

        await queue.put({
            "ticket_id": 9999, "source": "mention", "hint": "look at this",
        })

        with patch(
            "zd_agent.entry.saas_app.get_config",
            return_value=_make_cfg(),
        ):
            try:
                await asyncio.wait_for(
                    _trigger_consumer(queue, manager, state),
                    timeout=0.3,
                )
            except asyncio.TimeoutError:
                pass

        # mention → 直接 dispatch
        manager.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_not_skipped_when_no_state(self, tmp_path) -> None:
        """state.get(ticket_id) → None（新工单首次进 queue），不 skip"""
        state = _make_state(tmp_path)

        queue: asyncio.Queue = asyncio.Queue()
        manager = MagicMock()
        manager.dispatch = AsyncMock()

        await queue.put({
            "ticket_id": 9999, "source": "mention", "hint": "look",
        })

        with patch(
            "zd_agent.entry.saas_app.get_config",
            return_value=_make_cfg(),
        ):
            try:
                await asyncio.wait_for(
                    _trigger_consumer(queue, manager, state),
                    timeout=0.3,
                )
            except asyncio.TimeoutError:
                pass

        manager.dispatch.assert_called_once()
