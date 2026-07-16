"""Regression tests for messaging events that arrive before Zendesk creates a ticket."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, TriggerDeciderConfig, ZendeskConfig
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.trigger_consumer import run_trigger_consumer
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.trigger.decider import PreCheckVerdict
from zd_agent.trigger.state_store import TicketStateTracker


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=False)
    cfg.zendesk = ZendeskConfig(
        subdomain="x",
        email="x@y.com",
        api_token="t",
        min_ticket_id=0,
        agent_user_id=45433304089364,
    )
    cfg.trigger_decider = TriggerDeciderConfig(
        llm_model="gpt-test",
        post_agent_cooldown_seconds=180,
    )
    return cfg


@pytest.mark.asyncio
async def test_ticket_not_yet_materialized_is_deferred_without_consuming_retry(
    tmp_path: Path,
) -> None:
    """A messaging webhook may legitimately precede Zendesk ticket creation."""
    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg={"ticket_id": 3461, "source": "messaging_webhook"},
            manager=MagicMock(),
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(return_value=([], None)),
        )

    assert exc_info.value.reason == "ticket_not_materialized"
    assert exc_info.value.delay_seconds > 0


@pytest.mark.asyncio
async def test_materialization_wait_survives_five_fetch_misses_then_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five normal retries take about 75s; materialization waits must outlive them."""
    now = [1_000.0]
    queue = DurableTriggerQueue(
        tmp_path / "trigger_queue.sqlite3",
        max_attempts=5,
        retry_base_seconds=5,
        now_fn=lambda: now[0],
    )
    await queue.put({
        "ticket_id": 3461,
        "source": "messaging_webhook",
        "_queue_key": "messaging:3461:first-customer-message",
    })

    terminal = asyncio.Event()
    completed = False
    observed_queue_attempts: list[int] = []
    fetch_count = 0

    original_defer = queue.defer
    original_retry = queue.retry
    original_ack = queue.ack

    async def observed_defer(message: dict, delay_seconds: float, reason: str) -> None:
        await original_defer(message, delay_seconds, reason)
        now[0] += max(0.0, delay_seconds) + 0.001
        queue._wake.set()

    async def observed_retry(message: dict, error: str) -> bool:
        pending = await original_retry(message, error)
        retry_number = int(message.get("_queue_attempts") or 0) + 1
        now[0] += min(300.0, 5.0 * (2 ** max(0, retry_number - 1))) + 0.001
        queue._wake.set()
        if not pending:
            terminal.set()
        return pending

    async def observed_ack(message: dict) -> None:
        nonlocal completed
        await original_ack(message)
        completed = True
        terminal.set()

    monkeypatch.setattr(queue, "defer", observed_defer)
    monkeypatch.setattr(queue, "retry", observed_retry)
    monkeypatch.setattr(queue, "ack", observed_ack)

    async def fetch_customer_messages(_ticket_id: int):
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count <= 5:
            return [], None
        return (
            [{
                "id": "form-response-1",
                "body": "Workspace ID: 86628345-731d-4475-b681-5006f48a2287",
                "created_at": "2026-07-16T04:18:53Z",
            }],
            {"id": 3461, "_zd_agent_conversation_events": []},
        )

    verdict = PreCheckVerdict(
        verdict="substantial_issue",
        completeness=90,
        reason="form is complete",
    )
    post_verdict = AsyncMock()

    async def handle_with_materialization_fetch(**kwargs):
        observed_queue_attempts.append(int(kwargs["msg"].get("_queue_attempts") or 0))
        return await handle_trigger_message(
            **kwargs,
            fetch_customer_messages_fn=fetch_customer_messages,
            classify_ticket_messages_fn=AsyncMock(
                return_value=SimpleNamespace(verdict=verdict),
            ),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    task = asyncio.create_task(
        run_trigger_consumer(
            queue,
            MagicMock(),
            TicketStateTracker(tmp_path / "tickets.json"),
            cfg=_cfg(),
            env=SimpleNamespace(openai_api_key="sk-test"),
            log_path=tmp_path / "trigger.jsonl",
            handle_message_fn=handle_with_materialization_fetch,
            silence_expired_ask_fn=AsyncMock(),
            post_verdict_dispatch_fn=post_verdict,
            escalate_fn=AsyncMock(),
        )
    )

    await asyncio.wait_for(terminal.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert completed is True, "ticket materialization wait must not dead-letter"
    assert fetch_count == 6
    assert observed_queue_attempts == [0, 0, 0, 0, 0, 0]
    assert await queue.list_dead_letters() == []
    post_verdict.assert_awaited_once()

