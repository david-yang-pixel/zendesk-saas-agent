"""Tests for trigger verdict classification orchestration."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zd_agent.services.verdict_classification import classify_ticket_messages
from zd_agent.trigger.decider import PreCheckVerdict


@pytest.mark.asyncio
async def test_classify_ticket_messages_collects_images_and_calls_decider() -> None:
    verdict = PreCheckVerdict(
        verdict="substantial_issue",
        completeness=90,
        reason="has details",
    )
    collect_images = AsyncMock(return_value=["data:image/png;base64,AAA"])
    classify = AsyncMock(return_value=verdict)

    result = await classify_ticket_messages(
        ticket_id=12345,
        customer_msgs=[
            {"body": "first"},
            {"body": "second", "image_urls": ["https://example.com/a.png"]},
        ],
        model="gpt-test",
        api_key="sk-test",
        collect_recent_image_data_urls_fn=collect_images,
        classify_messages_fn=classify,
    )

    assert result.verdict is verdict
    assert result.images_used == 1
    collect_images.assert_awaited_once()
    assert collect_images.await_args.args[0][1]["body"] == "second"
    classify.assert_awaited_once_with(
        messages=["first", "second"],
        model="gpt-test",
        api_key="sk-test",
        image_data_urls=["data:image/png;base64,AAA"],
    )


@pytest.mark.asyncio
async def test_classify_ticket_messages_handles_missing_body() -> None:
    verdict = PreCheckVerdict(verdict="greeting", completeness=30, reason="hi")
    classify = AsyncMock(return_value=verdict)

    result = await classify_ticket_messages(
        ticket_id=12345,
        customer_msgs=[{}, {"body": None}],
        model="gpt-test",
        api_key="sk-test",
        collect_recent_image_data_urls_fn=AsyncMock(return_value=[]),
        classify_messages_fn=classify,
    )

    assert result.verdict.verdict == "greeting"
    classify.assert_awaited_once_with(
        messages=["", ""],
        model="gpt-test",
        api_key="sk-test",
        image_data_urls=[],
    )
