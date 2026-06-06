"""End-to-end integration tests covering the 6 customer scenarios for public reply.

每个场景模拟一个完整的 verdict → dispatch → state transition 链路，
但保持单元测试速度（LLM / Zendesk / Feishu 全部 mock）。

6 个核心客户场景（来自 2026-05-13 设计文档）：

1. **silent**            客户创建工单后 0 消息（仅 formResponse）
                        → A silent_template + state=waiting_for_issue + ask_count=1

2. **greeting only**     客户只发"hi / 在吗"
                        → B greeting_template + ask_count=1
                        → 第 2 轮还是 greeting → D retry_template + ask_count=2

3. **insufficient info** 客户说了但太模糊（"my app is broken"）
                        → C insufficient_info_template + ask_count=1
                        → 第 2 轮还是模糊 → D retry_template + ask_count=2

4. **substantial issue** 客户描述完整问题
                        → in_silence_window 等 burst 合并 + 不发 ask
                        → agent 后续 dispatch 处理（不在本 M9 范围）

5. **request human**     客户喊 "human" / "talk to a real person"
                        → 立即 state=escalated_to_human + customer_requested metric
                        → handoff_template 发出 + 飞书推送 + Zendesk tag

6. **self_resolved**     客户说"算了我搞定了 / never mind"
                        → state=processed（不发 ask 不发 handoff）

外加边界场景：
- **max_asks_reached**: ask_count=10 时再 insufficient → escalated (max_asks_reached)
- **high_risk**: substantial + refund → escalated (high_risk_refund)
- **reopen**: state=processed + 新客户消息 → customer_reopen metric
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, FeishuConfig, PublicReplyConfig, TriggerDeciderConfig, ZendeskConfig
from zd_agent.services.trigger_actions import post_verdict_dispatch_action
from zd_agent.trigger.decider import PreCheckVerdict
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _make_cfg(max_asks: int = 10, public_reply_enabled: bool = True) -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(
        enabled=public_reply_enabled, max_ask_count=max_asks,
    )
    cfg.trigger_decider = TriggerDeciderConfig(silence_minutes_default=3)
    cfg.feishu = FeishuConfig(webhook_url="", webhook_secret="")
    cfg.zendesk = ZendeskConfig(
        subdomain="dify-test", email="x@y.com", api_token="t",
    )
    return cfg


def _make_verdict(
    *, kind: str, completeness: int = 80,
    high_risk_category: str | None = None,
    reason: str = "",
) -> PreCheckVerdict:
    return PreCheckVerdict(
        verdict=kind,  # type: ignore[arg-type]
        completeness=completeness,
        reason=reason or f"test_{kind}",
        high_risk_category=high_risk_category,  # type: ignore[arg-type]
        high_risk_reason="" if high_risk_category is None else "test_risk",
    )


async def _run_dispatch(
    *,
    state: TicketStateTracker,
    verdict: PreCheckVerdict,
    ticket_id: int = 5000,
    existing: TicketState | None = None,
    cfg: AppConfig | None = None,
    customer_msgs: list[dict] | None = None,
    source: str = "zendesk_webhook",
    tmp_path,
    mock_send: AsyncMock | None = None,
) -> AsyncMock:
    """运行一次 dispatch，返回 send_ask_template 的 mock（供断言）。"""
    now = _now()
    log_path = Path(tmp_path) / "trigger.jsonl"
    cfg = cfg or _make_cfg()
    customer_msgs = customer_msgs or []
    mock_send = mock_send or AsyncMock(return_value=True)
    mock_send_pr = AsyncMock(return_value=(True, "ok"))

    with (
        patch(
            "zd_agent.tools.zendesk_public_reply.send_public_reply",
            mock_send_pr,
        ),
        patch("zd.client.ZendeskClient", return_value=MagicMock()),
        patch(
            "zd_agent.config.get_config", return_value=cfg,
        ),
    ):
        await post_verdict_dispatch_action(
            ticket_id=ticket_id,
            verdict=verdict,
            customer_msgs=customer_msgs,
            ticket_info={"id": ticket_id},
            latest_cid=f"cid-{ticket_id}",
            latest_ts=now,
            existing=existing,
            state=state,
            log_path=log_path,
            source=source,
            now=now,
            cfg=cfg,
            send_ask_fn=mock_send,
        )
    return mock_send


# ---------------------------------------------------------------------------
# Scenario 1: silent customer (no_message verdict)
# ---------------------------------------------------------------------------

class TestScenarioSilent:
    @pytest.mark.asyncio
    async def test_silent_first_visit_asks_silent_template(self, tmp_path) -> None:
        state = TicketStateTracker(tmp_path / "tickets.json")
        mock_send = await _run_dispatch(
            state=state,
            verdict=_make_verdict(kind="no_message", completeness=0),
            tmp_path=tmp_path,
        )
        rec = state.get(5000)
        assert rec.state == "waiting_for_issue"
        assert rec.ask_count == 1
        assert rec.last_verdict == "no_message"
        # send_ask_fn 被以 no_message + ask_count=0 调用
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["verdict_kind"] == "no_message"
        assert kwargs["ask_count"] == 0


# ---------------------------------------------------------------------------
# Scenario 2: greeting only (multi-round escalation B → D)
# ---------------------------------------------------------------------------

class TestScenarioGreetingMultiRound:
    @pytest.mark.asyncio
    async def test_greeting_first_round_uses_B(self, tmp_path) -> None:
        state = TicketStateTracker(tmp_path / "tickets.json")
        mock_send = await _run_dispatch(
            state=state,
            verdict=_make_verdict(kind="greeting"),
            tmp_path=tmp_path,
        )
        kwargs = mock_send.call_args.kwargs
        assert kwargs["verdict_kind"] == "greeting"
        assert kwargs["ask_count"] == 0  # 进 dispatch 时 ask_count 是 0
        assert state.get(5000).ask_count == 1

    @pytest.mark.asyncio
    async def test_greeting_second_round_uses_D(self, tmp_path) -> None:
        """第 2 轮还是 greeting → ask_count=1 进 dispatch → D retry"""
        state = TicketStateTracker(tmp_path / "tickets.json")
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=1,
            last_verdict="greeting",
        )
        state.set(5000, existing)
        mock_send = await _run_dispatch(
            state=state,
            verdict=_make_verdict(kind="greeting"),
            existing=existing,
            tmp_path=tmp_path,
        )
        kwargs = mock_send.call_args.kwargs
        assert kwargs["ask_count"] == 1  # D 模板触发条件
        assert state.get(5000).ask_count == 2

    @pytest.mark.asyncio
    async def test_greeting_at_max_asks_escalates(self, tmp_path) -> None:
        """第 11 次 greeting → max_asks_reached"""
        state = TicketStateTracker(tmp_path / "tickets.json")
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=10,
        )
        state.set(5000, existing)
        await _run_dispatch(
            state=state,
            verdict=_make_verdict(kind="greeting"),
            existing=existing,
            cfg=_make_cfg(max_asks=10),
            tmp_path=tmp_path,
        )
        rec = state.get(5000)
        assert rec.state == "escalated_to_human"
        assert rec.escalated_reason == "max_asks_reached"


# ---------------------------------------------------------------------------
# Scenario 3: insufficient info (multi-round C → D)
# ---------------------------------------------------------------------------

class TestScenarioInsufficientMultiRound:
    @pytest.mark.asyncio
    async def test_insufficient_first_round_uses_C(self, tmp_path) -> None:
        state = TicketStateTracker(tmp_path / "tickets.json")
        mock_send = await _run_dispatch(
            state=state,
            verdict=_make_verdict(kind="insufficient_info", completeness=30),
            customer_msgs=[{"id": "1", "body": "my app is broken"}],
            tmp_path=tmp_path,
        )
        kwargs = mock_send.call_args.kwargs
        assert kwargs["verdict_kind"] == "insufficient_info"
        assert kwargs["ask_count"] == 0  # 走 C 模板
        assert state.get(5000).ask_count == 1


# ---------------------------------------------------------------------------
# Scenario 4: substantial issue → in_silence_window
# ---------------------------------------------------------------------------

class TestScenarioSubstantial:
    @pytest.mark.asyncio
    async def test_substantial_safe_enters_silence(self, tmp_path) -> None:
        state = TicketStateTracker(tmp_path / "tickets.json")
        mock_send = await _run_dispatch(
            state=state,
            verdict=_make_verdict(kind="substantial_issue", completeness=90),
            customer_msgs=[{
                "id": "1",
                "body": "When I create a workflow I get a 500 error",
            }],
            tmp_path=tmp_path,
        )
        rec = state.get(5000)
        assert rec.state == "in_silence_window"
        assert rec.silence_window_end is not None
        # substantial 不走 ask template
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 5: request_human → immediate escalate + side effects
# ---------------------------------------------------------------------------

class TestScenarioRequestHuman:
    @pytest.mark.asyncio
    async def test_human_request_escalates_with_full_side_effects(
        self, tmp_path,
    ) -> None:
        """完整链路：state + handoff reply + customer_requested metric"""
        state = TicketStateTracker(tmp_path / "tickets.json")

        # 模拟客户最近说了 "human please"
        customer_msgs = [
            {"id": "10", "body": "Name: Alice\nWorkspace ID: ws-1"},
            {"id": "11", "body": "human please"},
        ]

        mock_send_pr = AsyncMock(return_value=(True, "ok"))
        mock_send_ask = AsyncMock(return_value=True)

        with (
            patch(
                "zd_agent.tools.zendesk_public_reply.send_public_reply",
                mock_send_pr,
            ),
            patch("zd.client.ZendeskClient", return_value=MagicMock()),
            patch(
                "zd_agent.observability.metrics.emit_customer_requested_human",
            ) as mock_metric,
            patch(
                "zd_agent.config.get_config",
                return_value=_make_cfg(public_reply_enabled=True),
            ),
        ):
            await post_verdict_dispatch_action(
                ticket_id=5000,
                verdict=_make_verdict(
                    kind="request_human", completeness=100,
                    reason="customer said human please",
                ),
                customer_msgs=customer_msgs,
                ticket_info={"id": 5000},
                latest_cid="cid-11", latest_ts=_now(),
                existing=None, state=state,
                log_path=Path(tmp_path) / "trigger.jsonl",
                source="zendesk_webhook",
                now=_now(),
                cfg=_make_cfg(public_reply_enabled=True),
                send_ask_fn=mock_send_ask,
            )

        # state 升级
        rec = state.get(5000)
        assert rec.state == "escalated_to_human"
        assert rec.escalated_reason == "customer_requested"

        # handoff 模板发了（reply_type=handoff，含 Alice）
        mock_send_pr.assert_called_once()
        kwargs = mock_send_pr.call_args.kwargs
        assert kwargs["reply_type"] == "handoff"
        assert "Alice" in kwargs["body"]

        # metric 发了
        mock_metric.assert_called_once()
        metric_kwargs = mock_metric.call_args.kwargs
        assert metric_kwargs["ticket_id"] == 5000

        # ask 模板没发（不是 ask 路径）
        mock_send_ask.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 6: self_resolved → processed
# ---------------------------------------------------------------------------

class TestScenarioSelfResolved:
    @pytest.mark.asyncio
    async def test_self_resolved_marks_processed_no_reply_sent(
        self, tmp_path,
    ) -> None:
        state = TicketStateTracker(tmp_path / "tickets.json")
        mock_send = await _run_dispatch(
            state=state,
            verdict=_make_verdict(kind="self_resolved", completeness=80),
            tmp_path=tmp_path,
        )
        rec = state.get(5000)
        assert rec.state == "processed"
        # 不发 ask 模板
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# Edge: high_risk → immediate escalate
# ---------------------------------------------------------------------------

class TestScenarioHighRisk:
    @pytest.mark.asyncio
    async def test_emotional_escalates_directly(self, tmp_path) -> None:
        """emotional → 直接升级人工（情绪客户必须人接手安抚，agent 写得再好都可能火上浇油）"""
        state = TicketStateTracker(tmp_path / "tickets.json")
        await _run_dispatch(
            state=state,
            verdict=_make_verdict(
                kind="substantial_issue", high_risk_category="emotional",
            ),
            tmp_path=tmp_path,
        )
        rec = state.get(5000)
        assert rec.state == "escalated_to_human"
        assert rec.escalated_reason == "high_risk_emotional"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("risk", ["refund", "incident"])
    async def test_high_risk_hint_falls_through_to_silence_window(
        self, tmp_path, risk: str,
    ) -> None:
        """refund / incident hint 不再 auto-escalate —— 让主 agent 查证据后决定。

        - refund: agent 查 DB+政策 → public_reply 告知客户判定 → 飞书推财务实操
        - incident: agent 用 SigNoz 验证是不是真 platform-wide 故障
        """
        state = TicketStateTracker(tmp_path / "tickets.json")
        await _run_dispatch(
            state=state,
            verdict=_make_verdict(
                kind="substantial_issue", high_risk_category=risk,
            ),
            tmp_path=tmp_path,
        )
        rec = state.get(5000)
        assert rec.state == "in_silence_window"
        assert rec.escalated_reason == ""


# ---------------------------------------------------------------------------
# Cross-scenario: ask_count 不会因发送失败而误增长
# ---------------------------------------------------------------------------

class TestAskCountSafetyAcrossRounds:
    @pytest.mark.asyncio
    async def test_failed_send_no_ask_count_inflation(self, tmp_path) -> None:
        """ask_count 计的是真的发了的轮次。kill switch 关 / API 失败 → 不该 +1
        （防止 N 次失败把客户卡死在 max_asks_reached）"""
        state = TicketStateTracker(tmp_path / "tickets.json")
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=5,
        )
        state.set(5000, existing)
        await _run_dispatch(
            state=state,
            verdict=_make_verdict(kind="greeting"),
            existing=existing,
            tmp_path=tmp_path,
            mock_send=AsyncMock(return_value=False),  # 模拟发送失败
        )
        # 5 → 5（没涨）
        assert state.get(5000).ask_count == 5
