"""Tests for the active-ticket intake backfill poller."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from zd_agent.services.intake_poller import (
    IntakeSearchProgress,
    run_intake_poller,
    scan_once,
)


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
async def test_scan_processes_searches_over_ten_pages_in_bounded_batches() -> None:
    async def call(_name: str, arguments: dict) -> dict:
        page = arguments["page"]
        return {
            "success": True,
            "data": {
                "results": [{"id": 5000 + page, "updated_at": f"rev-{page}"}],
                "next_page": f"page-{page + 1}" if page < 11 else None,
            },
        }

    queue = AsyncMock()
    queue.put.return_value = True
    cache = {9000: "previously-visible"}
    progress = IntakeSearchProgress()

    first = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache=cache,
        call_fn=call,
        search_progress=progress,
    )

    assert first == 10
    assert progress.next_page == 11
    assert 9000 in cache

    second = await scan_once(
        trigger_queue=queue,
        query="status<solved tags:saas",
        ticket_update_cache=cache,
        call_fn=call,
        search_progress=progress,
    )

    assert second == 1
    assert progress.next_page == 1
    assert set(cache) == set(range(5001, 5012))
    assert queue.put.await_count == 11


@pytest.mark.asyncio
async def test_startup_baseline_over_ten_pages_does_not_enqueue_existing_tickets(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ZD_AGENT_ACCEPT_EVENTS_AFTER", raising=False)

    async def call(_name: str, arguments: dict) -> dict:
        page = arguments["page"]
        return {
            "success": True,
            "data": {
                "results": [
                    {
                        "id": 6000 + page,
                        "updated_at": f"2026-08-{page:02d}T00:00:00Z",
                    }
                ],
                "next_page": f"page-{page + 1}" if page < 11 else None,
            },
        }

    class Queue:
        path = tmp_path / "trigger_queue.sqlite3"

        def __init__(self) -> None:
            self.items: list[dict] = []

        async def put(self, item: dict) -> bool:
            self.items.append(item)
            return True

    queue = Queue()
    monkeypatch.setattr(
        "zd_agent.services.intake_poller.call_zendesk_tool",
        call,
    )

    async def stop_after_first_scan(_delay: int) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "zd_agent.services.intake_poller.asyncio.sleep",
        stop_after_first_scan,
    )

    with pytest.raises(asyncio.CancelledError):
        await run_intake_poller(
            trigger_queue=queue,
            query="status<solved tags:saas",
            poll_interval_seconds=5,
        )

    assert queue.items == []
    cache = json.loads((tmp_path / "intake_revisions.json").read_text())
    assert set(cache) == {str(ticket_id) for ticket_id in range(6001, 6012)}


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
