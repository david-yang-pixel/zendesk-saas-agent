"""Tests for _handle_post_verdict_dispatch (M4 main logic).

Verdict 分发覆盖：
1. request_human                           → escalated_to_human (customer_requested)
2. self_resolved                           → state=processed
3. substantial + high_risk={refund/...}    → escalated_to_human (high_risk_*)
4. substantial + safe                      → in_silence_window
5. ask path + ask_count >= max_ask_count   → escalated_to_human (max_asks_reached)
6. ask path + ask_count < max_ask_count    → waiting_for_issue + ask_count++

也包括 no_message / greeting / insufficient_info 都正确归到 ask 路径。
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, TriggerDeciderConfig
from zd_agent.entry.saas_app import _handle_post_verdict_dispatch
from zd_agent.trigger.decider import PreCheckVerdict
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


# ---------- helpers ----------

def _now():
    return datetime.now(timezone.utc)


def _make_cfg(max_asks: int = 10, silence_min: int = 3) -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(max_ask_count=max_asks)
    cfg.trigger_decider = TriggerDeciderConfig(
        silence_minutes_default=silence_min,
    )
    return cfg


def _make_state(tmp_path) -> TicketStateTracker:
    return TicketStateTracker(tmp_path / "tickets.json")


def _make_verdict(
    kind: str = "substantial_issue",
    completeness: int = 80,
    reason: str = "test",
    high_risk_category: str | None = None,
    high_risk_reason: str = "",
) -> PreCheckVerdict:
    return PreCheckVerdict(
        verdict=kind,  # type: ignore[arg-type]
        completeness=completeness,
        reason=reason,
        high_risk_category=high_risk_category,  # type: ignore[arg-type]
        high_risk_reason=high_risk_reason,
    )


async def _dispatch(
    *,
    state: TicketStateTracker,
    verdict: PreCheckVerdict,
    ticket_id: int = 12345,
    existing: TicketState | None = None,
    cfg: AppConfig | None = None,
    source: str = "zendesk_webhook",
    tmp_path=None,
) -> None:
    """共用 helper：调用 dispatch 并补默认参数。"""
    from pathlib import Path
    now = _now()
    log_path = (
        Path(tmp_path) / "trigger.jsonl"
        if tmp_path is not None
        else Path("/tmp/trigger_test.jsonl")
    )
    await _handle_post_verdict_dispatch(
        ticket_id=ticket_id,
        verdict=verdict,
        customer_msgs=[],
        ticket_info={"id": ticket_id},
        latest_cid="cid-1",
        latest_ts=now,
        existing=existing,
        state=state,
        log_path=log_path,
        source=source,
        now=now,
        cfg=cfg or _make_cfg(),
    )


# ============================================================================
# Priority 1: request_human → escalated_to_human
# ============================================================================

class TestRequestHumanEscalates:
    @pytest.mark.asyncio
    async def test_request_human_escalates_immediately(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        await _dispatch(
            state=state,
            verdict=_make_verdict(kind="request_human", completeness=100,
                                   reason="customer said human"),
            tmp_path=tmp_path,
        )
        rec = state.get(12345)
        assert rec.state == "escalated_to_human"
        assert rec.escalated_reason == "customer_requested"
        assert rec.escalated_at is not None
        assert rec.last_verdict == "request_human"

    @pytest.mark.asyncio
    async def test_request_human_preserves_ask_count(self, tmp_path) -> None:
        """已问过几次后客户说 human → 升级时 ask_count 保留（事后审计）"""
        state = _make_state(tmp_path)
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=3,
        )
        state.set(12345, existing)
        await _dispatch(
            state=state, verdict=_make_verdict(kind="request_human"),
            existing=existing, tmp_path=tmp_path,
        )
        assert state.get(12345).ask_count == 3


# ============================================================================
# Priority 2: self_resolved → processed
# ============================================================================

class TestSelfResolved:
    @pytest.mark.asyncio
    async def test_self_resolved_marks_processed(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        await _dispatch(
            state=state,
            verdict=_make_verdict(kind="self_resolved", completeness=85),
            tmp_path=tmp_path,
        )
        rec = state.get(12345)
        assert rec.state == "processed"
        assert rec.last_verdict == "self_resolved"
        assert rec.escalated_reason == ""  # 不是升级


# ============================================================================
# Priority 3: substantial + high_risk → escalated
# ============================================================================

class TestHighRiskEscalation:
    @pytest.mark.asyncio
    async def test_refund_escalates(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        await _dispatch(
            state=state,
            verdict=_make_verdict(
                kind="substantial_issue",
                high_risk_category="refund",
                high_risk_reason="客户要退款 $200",
            ),
            tmp_path=tmp_path,
        )
        rec = state.get(12345)
        assert rec.state == "escalated_to_human"
        assert rec.escalated_reason == "high_risk_refund"

    @pytest.mark.asyncio
    async def test_emotional_escalates(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        await _dispatch(
            state=state,
            verdict=_make_verdict(
                kind="substantial_issue",
                high_risk_category="emotional",
            ),
            tmp_path=tmp_path,
        )
        assert state.get(12345).escalated_reason == "high_risk_emotional"

    @pytest.mark.asyncio
    async def test_incident_escalates(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        await _dispatch(
            state=state,
            verdict=_make_verdict(
                kind="substantial_issue",
                high_risk_category="incident",
            ),
            tmp_path=tmp_path,
        )
        assert state.get(12345).escalated_reason == "high_risk_incident"


# ============================================================================
# Priority 4: substantial + safe → in_silence_window (legacy path unchanged)
# ============================================================================

class TestSubstantialIssueEntersSilence:
    @pytest.mark.asyncio
    async def test_safe_substantial_enters_silence(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        await _dispatch(
            state=state,
            verdict=_make_verdict(kind="substantial_issue", completeness=85),
            tmp_path=tmp_path,
        )
        rec = state.get(12345)
        assert rec.state == "in_silence_window"
        assert rec.silence_window_end is not None
        assert rec.escalated_reason == ""  # 不是升级


# ============================================================================
# Priority 5: ask path + ask_count >= max → escalate
# ============================================================================

class TestAskCountLimit:
    @pytest.mark.asyncio
    async def test_ask_count_at_max_escalates(self, tmp_path) -> None:
        """ask_count=10 已经到上限，再来 insufficient → 升级"""
        state = _make_state(tmp_path)
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=10,
        )
        state.set(12345, existing)
        cfg = _make_cfg(max_asks=10)
        await _dispatch(
            state=state, verdict=_make_verdict(kind="insufficient_info"),
            existing=existing, cfg=cfg, tmp_path=tmp_path,
        )
        rec = state.get(12345)
        assert rec.state == "escalated_to_human"
        assert rec.escalated_reason == "max_asks_reached"

    @pytest.mark.asyncio
    async def test_ask_count_above_max_also_escalates(self, tmp_path) -> None:
        """虽然不应该出现，但 ask_count > max 也应该正确升级"""
        state = _make_state(tmp_path)
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=15,
        )
        state.set(12345, existing)
        await _dispatch(
            state=state, verdict=_make_verdict(kind="greeting"),
            existing=existing, cfg=_make_cfg(max_asks=10), tmp_path=tmp_path,
        )
        assert state.get(12345).escalated_reason == "max_asks_reached"


# ============================================================================
# Priority 6: ask path + ask_count < max → waiting_for_issue + ask_count++
# ============================================================================

class TestAskPath:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("verdict_kind", [
        "no_message", "greeting", "insufficient_info",
    ])
    async def test_ask_path_increments_count_from_zero(
        self, tmp_path, verdict_kind: str,
    ) -> None:
        """三种 ask 类 verdict 都走 ask 路径 + ask_count 从 0 加到 1"""
        state = _make_state(tmp_path)
        await _dispatch(
            state=state, verdict=_make_verdict(kind=verdict_kind),
            tmp_path=tmp_path,
        )
        rec = state.get(12345)
        assert rec.state == "waiting_for_issue"
        assert rec.ask_count == 1
        assert rec.last_verdict == verdict_kind

    @pytest.mark.asyncio
    async def test_ask_count_increments_each_round(self, tmp_path) -> None:
        """第 3 次 ask 后客户还说 greeting → ask_count 4"""
        state = _make_state(tmp_path)
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=3,
        )
        state.set(12345, existing)
        await _dispatch(
            state=state, verdict=_make_verdict(kind="greeting"),
            existing=existing, tmp_path=tmp_path,
        )
        assert state.get(12345).ask_count == 4

    @pytest.mark.asyncio
    async def test_ask_path_just_below_max_still_asks(self, tmp_path) -> None:
        """ask_count=9 + max=10 → 还能再问一次"""
        state = _make_state(tmp_path)
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=9,
        )
        state.set(12345, existing)
        await _dispatch(
            state=state, verdict=_make_verdict(kind="insufficient_info"),
            existing=existing, cfg=_make_cfg(max_asks=10), tmp_path=tmp_path,
        )
        rec = state.get(12345)
        assert rec.state == "waiting_for_issue"
        assert rec.ask_count == 10  # 这次是第 10 次（最后一次）

    @pytest.mark.asyncio
    async def test_send_ask_template_stub_called(self, tmp_path) -> None:
        """验证 _send_ask_template stub 被调用（M4 阶段只 log 不真发）"""
        state = _make_state(tmp_path)
        with patch(
            "zd_agent.entry.saas_app._send_ask_template",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send:
            await _dispatch(
                state=state,
                verdict=_make_verdict(kind="greeting"),
                tmp_path=tmp_path,
            )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["ticket_id"] == 12345
        assert kwargs["verdict_kind"] == "greeting"
        assert kwargs["ask_count"] == 0  # 调用前的值

    @pytest.mark.asyncio
    async def test_send_ask_template_returns_false_no_increment(self, tmp_path) -> None:
        """模拟 M6 实施后发送失败：ask_count 不该累加（避免空跑也吞 quota）"""
        state = _make_state(tmp_path)
        existing = TicketState(
            state="waiting_for_issue", first_seen=_now(), ask_count=3,
        )
        state.set(12345, existing)
        with patch(
            "zd_agent.entry.saas_app._send_ask_template",
            new_callable=AsyncMock,
            return_value=False,
        ):
            await _dispatch(
                state=state, verdict=_make_verdict(kind="greeting"),
                existing=existing, tmp_path=tmp_path,
            )
        # 发送失败 → ask_count 保持 3 不变
        assert state.get(12345).ask_count == 3


# ============================================================================
# Backward compat: existing legacy path (substantial_issue + safe) unchanged
# ============================================================================

class TestBackwardCompat:
    @pytest.mark.asyncio
    async def test_legacy_substantial_silence_window_min_respected(
        self, tmp_path,
    ) -> None:
        """silence_window_end = now + silence_min minutes（保留旧行为）"""
        from datetime import timedelta
        state = _make_state(tmp_path)
        cfg = _make_cfg(silence_min=5)
        await _dispatch(
            state=state,
            verdict=_make_verdict(kind="substantial_issue"),
            cfg=cfg, tmp_path=tmp_path,
        )
        rec = state.get(12345)
        assert rec.state == "in_silence_window"
        # silence_window_end - now ≈ 5 分钟（容忍小误差）
        delta = rec.silence_window_end - rec.last_eval_at
        assert timedelta(minutes=4, seconds=58) < delta < timedelta(minutes=5, seconds=2)
