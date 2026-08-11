"""Tests for direct trigger dispatch sources."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from zd_agent.casework.domain.enums import (
    OwnershipAction,
    OwnershipState,
    ReasonCode,
)
from zd_agent.services.direct_dispatch import handle_direct_dispatch_source
from zd_agent.services.casework_runtime_failures import (
    RuntimeFailureOwnershipReceipt,
)
from zd_agent.session.agent_session import AgentSession
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now() -> datetime:
    return datetime(2026, 6, 5, 13, 45, tzinfo=timezone.utc)


def _cfg(*, cooldown_seconds: int = 180) -> MagicMock:
    cfg = MagicMock()
    cfg.intake.post_agent_cooldown_seconds = cooldown_seconds
    cfg.resources.base_dir = "/tmp/zd-agent-test"
    cfg.resources.state_dir = "state"
    return cfg


@pytest.mark.asyncio
async def test_non_direct_source_is_not_handled(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()

    result = await handle_direct_dispatch_source(
        ticket_id=12345,
        msg={"source": "zendesk_webhook"},
        manager=manager,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(),
        now=_now(),
    )

    assert result.handled is False
    assert result.dispatched is False
    manager.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_recovery_reenters_normal_takeover_gates(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()

    result = await handle_direct_dispatch_source(
        ticket_id=12345,
        msg={"source": "startup_recovery"},
        manager=manager,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(),
        now=_now(),
    )

    assert result.handled is False
    assert result.dispatched is False
    manager.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_source_dispatches_with_hint(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()

    async def dispatch(ticket_id: int, **kwargs):
        session = AgentSession(ticket_id=ticket_id, session_id="session-created")
        kwargs["on_session_created"](session)
        return session

    manager.dispatch = AsyncMock(side_effect=dispatch)

    result = await handle_direct_dispatch_source(
        ticket_id=12345,
        msg={"source": "mention", "hint": "please rerun"},
        manager=manager,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(),
        now=_now(),
    )

    assert result.handled is True
    assert result.dispatched is True
    assert result.action == "dispatch"
    assert state.get(12345).state == "processing"
    assert state.get(12345).pending_trigger_hint == "please rerun"
    manager.dispatch.assert_awaited_once_with(
        12345,
        dry_run=False,
        trigger_type="webhook-mention",
        hint="please rerun",
        delivery_attempt_id="mention:12345",
        on_session_created=ANY,
    )


@pytest.mark.asyncio
async def test_silence_retry_binds_the_durable_attempt_to_v2_session(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="in_silence_window",
            first_seen=_now() - timedelta(minutes=10),
            silence_window_end=_now() - timedelta(seconds=1),
            last_verdict="agent_retry",
            consecutive_agent_failures=2,
            pending_customer_fields=("target_workspace_choice",),
            pending_issue_question="Please approve a refund for this renewal.",
        ),
    )
    current = state.get(12345)
    assert current is not None
    sessions: list[AgentSession] = []
    manager = MagicMock()

    async def dispatch(ticket_id: int, **kwargs):
        session = AgentSession(ticket_id=ticket_id, session_id="session-created")
        kwargs["on_session_created"](session)
        sessions.append(session)
        return session

    manager.dispatch = AsyncMock(side_effect=dispatch)

    result = await handle_direct_dispatch_source(
        ticket_id=12345,
        msg={
            "source": "silence_waker",
            "expected_state_revision": current.state_revision,
            "expected_silence_window_end": current.silence_window_end.isoformat(),
        },
        manager=manager,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(cooldown_seconds=0),
        now=_now(),
    )

    assert result.dispatched is True
    assert sessions[0].casework_attempt == 3
    assert sessions[0].prior_customer_required_fields == ("target_workspace_choice",)
    assert (
        sessions[0].prior_issue_question == "Please approve a refund for this renewal."
    )


@pytest.mark.asyncio
async def test_direct_source_cooldown_skips_dispatch_and_logs_decision(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="processed",
            first_seen=_now() - timedelta(minutes=10),
            last_agent_finish_ts=_now() - timedelta(seconds=20),
        ),
    )
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    log_path = tmp_path / "trigger.jsonl"

    result = await handle_direct_dispatch_source(
        ticket_id=12345,
        msg={"source": "mention", "hint": "please rerun"},
        manager=manager,
        state=state,
        log_path=log_path,
        cfg=_cfg(),
        now=_now(),
    )

    assert result.handled is True
    assert result.dispatched is False
    assert result.action == "skip_post_agent_cooldown"
    assert result.cooldown_seconds == 30
    manager.dispatch.assert_not_awaited()

    [record] = log_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(record)
    assert payload["ticket"] == "12345"
    assert payload["verdict"] == "post_agent_cooldown_mention"
    assert payload["action"] == "skip"


@pytest.mark.asyncio
async def test_silence_waker_cooldown_requeues_instead_of_sticking_processing(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        3436,
        TicketState(
            state="in_silence_window",
            first_seen=_now() - timedelta(minutes=20),
            silence_window_end=_now() - timedelta(seconds=10),
            last_verdict="substantial_issue",
            last_agent_finish_ts=_now() - timedelta(seconds=20),
        ),
    )
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    log_path = tmp_path / "trigger.jsonl"

    result = await handle_direct_dispatch_source(
        ticket_id=3436,
        msg={
            "source": "silence_waker",
            "expected_state_revision": state.get(3436).state_revision,
            "expected_silence_window_end": (
                state.get(3436).silence_window_end.isoformat()
            ),
        },
        manager=manager,
        state=state,
        log_path=log_path,
        cfg=_cfg(cooldown_seconds=180),
        now=_now(),
    )

    assert result.handled is True
    assert result.dispatched is False
    assert result.action == "defer_post_agent_cooldown"
    assert result.cooldown_seconds == 180
    restored = state.get(3436)
    assert restored is not None
    assert restored.state == "in_silence_window"
    assert restored.silence_window_end == _now() + timedelta(seconds=160)
    manager.dispatch.assert_not_awaited()

    [record] = log_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(record)
    assert payload["verdict"] == "post_agent_cooldown_silence_waker"
    assert payload["action"] == "defer"


@pytest.mark.asyncio
async def test_repeated_waker_dispatch_failures_have_durable_terminal_budget(
    tmp_path,
) -> None:
    state_path = tmp_path / "tickets.json"
    state = TicketStateTracker(state_path)
    state.set(
        3436,
        TicketState(
            state="in_silence_window",
            first_seen=_now() - timedelta(minutes=20),
            silence_window_end=_now() - timedelta(seconds=1),
            last_verdict="agent_retry",
            delivery_attempt_id="delivery-stable",
        ),
    )
    manager = MagicMock(dispatch=AsyncMock(side_effect=RuntimeError("no worker")))
    own_failure = AsyncMock(
        return_value=RuntimeFailureOwnershipReceipt(
            reference="casework-effect:dispatch",
            ownership_action=OwnershipAction.SUPPORT,
            reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
            ownership_state=OwnershipState.CONFIRMED,
        ),
    )

    for attempt in range(1, 4):
        state = TicketStateTracker(state_path)
        current = state.get(3436)
        assert current is not None
        message = {
            "source": "silence_waker",
            "delivery_attempt_id": "delivery-stable",
            "expected_state_revision": current.state_revision,
            "expected_silence_window_end": (
                current.silence_window_end.isoformat()
                if current.silence_window_end
                else ""
            ),
        }
        if attempt < 3:
            with pytest.raises(RuntimeError, match="no worker"):
                await handle_direct_dispatch_source(
                    ticket_id=3436,
                    msg=message,
                    manager=manager,
                    state=state,
                    log_path=tmp_path / "trigger.jsonl",
                    cfg=_cfg(cooldown_seconds=0),
                    now=_now() + timedelta(minutes=attempt),
                    runtime_failure_ownership_fn=own_failure,
                )
            retry = TicketStateTracker(state_path).get(3436)
            assert retry is not None
            assert retry.state == "in_silence_window"
            assert retry.consecutive_agent_failures == attempt
            retry.silence_window_end = (
                _now()
                + timedelta(minutes=attempt)
                - timedelta(
                    seconds=1,
                )
            )
            TicketStateTracker(state_path).set(3436, retry)
        else:
            result = await handle_direct_dispatch_source(
                ticket_id=3436,
                msg=message,
                manager=manager,
                state=state,
                log_path=tmp_path / "trigger.jsonl",
                cfg=_cfg(cooldown_seconds=0),
                now=_now() + timedelta(minutes=attempt),
                runtime_failure_ownership_fn=own_failure,
            )
            assert result.action == "owned_dispatch_retry_exhausted"

    terminal = TicketStateTracker(state_path).get(3436)
    assert terminal is not None
    assert terminal.state == "owned"
    assert terminal.ownership_action == "support"
    assert terminal.ownership_reason == "automation_retry_exhausted"
    assert terminal.ownership_effect_reference == "casework-effect:dispatch"
    assert terminal.delivery_attempt_id == "delivery-stable"
    own_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_dispatch_failure_cannot_overwrite_new_customer_generation(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        3436,
        TicketState(
            state="in_silence_window",
            first_seen=_now() - timedelta(minutes=20),
            silence_window_end=_now() - timedelta(seconds=1),
            last_customer_comment_id="m1",
            delivery_attempt_id="D1",
            consecutive_agent_failures=2,
        ),
    )
    initial = state.get(3436)
    assert initial is not None

    class Manager:
        async def dispatch(self, ticket_id: int, **kwargs):
            session = AgentSession(ticket_id=ticket_id)
            kwargs["on_session_created"](session)
            raise RuntimeError("no worker")

    entered = asyncio.Event()
    resume = asyncio.Event()

    async def own_failure(**_kwargs) -> RuntimeFailureOwnershipReceipt:
        entered.set()
        await resume.wait()
        return RuntimeFailureOwnershipReceipt(
            reference="casework-effect:dispatch-old",
            ownership_action=OwnershipAction.SUPPORT,
            reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
            ownership_state=OwnershipState.CONFIRMED,
        )

    task = asyncio.create_task(
        handle_direct_dispatch_source(
            ticket_id=3436,
            msg={
                "source": "silence_waker",
                "delivery_attempt_id": "D1",
                "expected_state_revision": initial.state_revision,
                "expected_silence_window_end": initial.silence_window_end.isoformat(),
            },
            manager=Manager(),
            state=state,
            log_path=tmp_path / "trigger.jsonl",
            cfg=_cfg(cooldown_seconds=0),
            now=_now(),
            runtime_failure_ownership_fn=own_failure,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    newer = state.get(3436)
    assert newer is not None and newer.state == "ownership_pending"
    newer.state = "awaiting_customer"
    newer.active_session_id = ""
    newer.last_customer_comment_id = "m2"
    newer.delivery_attempt_id = "D2"
    state.set(3436, newer)
    resume.set()

    result = await task
    persisted = state.get(3436)
    assert result.action == "stale_dispatch_failure_ownership"
    assert persisted is not None
    assert persisted.state == "awaiting_customer"
    assert persisted.last_customer_comment_id == "m2"
    assert persisted.delivery_attempt_id == "D2"


@pytest.mark.asyncio
async def test_pending_dispatch_ownership_persists_reconciliation_authority(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        3437,
        TicketState(
            state="in_silence_window",
            first_seen=_now() - timedelta(minutes=20),
            silence_window_end=_now() - timedelta(seconds=1),
            last_customer_comment_id="m1",
            delivery_attempt_id="D1",
            consecutive_agent_failures=2,
        ),
    )
    initial = state.get(3437)
    assert initial is not None

    class Manager:
        async def dispatch(self, ticket_id: int, **kwargs):
            session = AgentSession(ticket_id=ticket_id)
            kwargs["on_session_created"](session)
            raise RuntimeError("no worker")

    from zd_agent.services.casework_effect_store import casework_effect_reference

    reference = casework_effect_reference(3437, "D1:runtime-failure")
    own_failure = AsyncMock(
        return_value=RuntimeFailureOwnershipReceipt(
            reference=reference,
            ownership_action=OwnershipAction.SUPPORT,
            reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
            ownership_state=OwnershipState.PENDING,
        ),
    )

    result = await handle_direct_dispatch_source(
        ticket_id=3437,
        msg={
            "source": "silence_waker",
            "delivery_attempt_id": "D1",
            "expected_state_revision": initial.state_revision,
            "expected_silence_window_end": initial.silence_window_end.isoformat(),
        },
        manager=Manager(),
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(cooldown_seconds=0),
        now=_now(),
        runtime_failure_ownership_fn=own_failure,
    )

    pending = state.get(3437)
    assert result.action == "ownership_pending_dispatch_retry_exhausted"
    assert pending is not None and pending.state == "ownership_pending"
    assert pending.ownership_action == "support"
    assert pending.ownership_reason == "automation_retry_exhausted"
    assert pending.ownership_effect_reference == reference


@pytest.mark.asyncio
async def test_silence_replay_resumes_crash_after_ownership_claim(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        3436,
        TicketState(
            state="ownership_pending",
            first_seen=_now() - timedelta(minutes=20),
            active_session_id="dispatch-failure:D1:3",
            last_customer_comment_id="m1",
            delivery_attempt_id="D1",
            consecutive_agent_failures=3,
            last_verdict="automation_retry_exhausted_pending",
        ),
    )
    manager = MagicMock()
    own_failure = AsyncMock(
        return_value=RuntimeFailureOwnershipReceipt(
            reference="casework-effect:dispatch-resumed",
            ownership_action=OwnershipAction.SUPPORT,
            reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
            ownership_state=OwnershipState.CONFIRMED,
        )
    )

    result = await handle_direct_dispatch_source(
        ticket_id=3436,
        msg={"source": "silence_waker", "delivery_attempt_id": "D1"},
        manager=manager,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(cooldown_seconds=0),
        now=_now(),
        runtime_failure_ownership_fn=own_failure,
    )

    persisted = state.get(3436)
    assert result.action == "owned_dispatch_retry_exhausted"
    assert persisted is not None and persisted.state == "owned"
    assert persisted.ownership_effect_reference == "casework-effect:dispatch-resumed"
    own_failure.assert_awaited_once()
    manager.dispatch.assert_not_called()
