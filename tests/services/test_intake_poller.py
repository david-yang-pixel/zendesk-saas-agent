"""Tests for the active-ticket intake backfill poller."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zd_agent.services.intake_poller import scan_once


@pytest.mark.asyncio
async def test_scan_enqueues_each_updated_active_ticket_with_a_durable_key() -> None:
    call = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "results": [
                    {"id": 4049, "updated_at": "2026-08-26T07:00:00Z"},
                    {"id": "4051", "updated_at": "2026-08-26T07:01:00Z"},
                ],
            },
        },
    )
    queue = AsyncMock()
    queue.put.return_value = True
    update_cache: dict[int, str] = {}

    dispatched = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache=update_cache,
        call_fn=call,
    )

    assert dispatched == 2
    call.assert_awaited_once_with(
        "search_tickets",
        {"query": "status<solved tags:saas", "page": 1, "per_page": 100},
    )
    assert [item.args[0] for item in queue.put.await_args_list] == [
        {
            "source": "intake_backfill",
            "ticket_id": 4049,
            "_queue_key": "intake_backfill:4049:2026-08-26T07:00:00Z",
        },
        {
            "source": "intake_backfill",
            "ticket_id": 4051,
            "_queue_key": "intake_backfill:4051:2026-08-26T07:01:00Z",
        },
    ]


@pytest.mark.asyncio
async def test_unchanged_ticket_is_not_reenqueued_on_every_scan() -> None:
    call = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "results": [
                    {"id": 4049, "updated_at": "2026-08-26T07:00:00Z"},
                ],
            },
        },
    )
    queue = AsyncMock()
    queue.put.return_value = True
    update_cache: dict[int, str] = {}

    first = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache=update_cache,
        call_fn=call,
    )
    second = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache=update_cache,
        call_fn=call,
    )

    assert first == 1
    assert second == 0
    queue.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_queue_write_keeps_ticket_retryable() -> None:
    call = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "results": [
                    {"id": 4049, "updated_at": "2026-08-26T07:00:00Z"},
                ],
            },
        },
    )
    queue = AsyncMock()
    queue.put.side_effect = [OSError("sqlite busy"), True]
    update_cache: dict[int, str] = {}

    first = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache=update_cache,
        call_fn=call,
    )
    second = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache=update_cache,
        call_fn=call,
    )

    assert first == 0
    assert second == 1
    assert queue.put.await_count == 2


@pytest.mark.asyncio
async def test_scan_paginates_all_active_ticket_results() -> None:
    call = AsyncMock(
        side_effect=[
            {
                "success": True,
                "data": {
                    "results": [{"id": 5001, "updated_at": "rev-1"}],
                    "next_page": "https://zendesk.example/search?page=2",
                },
            },
            {
                "success": True,
                "data": {
                    "results": [{"id": 5002, "updated_at": "rev-2"}],
                    "next_page": None,
                },
            },
        ]
    )
    queue = AsyncMock()
    queue.put.return_value = True

    dispatched = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache={},
        call_fn=call,
    )

    assert dispatched == 2
    assert call.await_args_list[1].args == (
        "search_tickets",
        {"query": "status<solved tags:saas", "page": 2, "per_page": 100},
    )


@pytest.mark.asyncio
async def test_online_watermark_primes_old_ticket_without_backfill(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZD_AGENT_ACCEPT_EVENTS_AFTER", "1787702400")
    call = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "results": [
                    {"id": 4049, "updated_at": "2026-08-25T23:59:59Z"},
                    {"id": 4051, "updated_at": "2026-08-26T00:00:01Z"},
                ]
            },
        }
    )
    queue = AsyncMock()
    queue.put.return_value = True
    cache: dict[int, str] = {}

    dispatched = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache=cache,
        call_fn=call,
    )

    assert dispatched == 1
    assert cache[4049] == "2026-08-25T23:59:59Z"
    assert queue.put.await_args.args[0]["ticket_id"] == 4051
