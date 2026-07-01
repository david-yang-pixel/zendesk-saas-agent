from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zd_agent.services.trigger_delay import (
    wait_for_trigger_availability,
    with_trigger_delay,
)


def test_with_trigger_delay_adds_available_at() -> None:
    msg = with_trigger_delay(
        {"ticket_id": "3195", "source": "messaging_webhook"},
        delay_seconds=30,
        now_fn=lambda: 1000.0,
    )

    assert msg["available_at"] == 1030.0
    assert msg["ticket_id"] == "3195"


def test_with_trigger_delay_omits_available_at_for_zero_delay() -> None:
    msg = with_trigger_delay(
        {"ticket_id": "3195", "source": "messaging_webhook"},
        delay_seconds=0,
        now_fn=lambda: 1000.0,
    )

    assert "available_at" not in msg


@pytest.mark.asyncio
async def test_wait_for_trigger_availability_sleeps_until_available() -> None:
    sleep = AsyncMock()

    waited = await wait_for_trigger_availability(
        {"available_at": 1030.0},
        sleep_fn=sleep,
        now_fn=lambda: 1000.0,
    )

    assert waited == 30.0
    sleep.assert_awaited_once_with(30.0)


@pytest.mark.asyncio
async def test_wait_for_trigger_availability_skips_expired_message() -> None:
    sleep = AsyncMock()

    waited = await wait_for_trigger_availability(
        {"available_at": 990.0},
        sleep_fn=sleep,
        now_fn=lambda: 1000.0,
    )

    assert waited == 0.0
    sleep.assert_not_awaited()
