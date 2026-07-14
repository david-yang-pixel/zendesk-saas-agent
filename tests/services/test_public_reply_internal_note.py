from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zd_agent.services.public_reply_internal_note import (
    build_post_public_internal_note,
    ensure_post_public_internal_note,
)


@pytest.mark.asyncio
async def test_existing_reference_skips_duplicate_internal_note() -> None:
    note = build_post_public_internal_note(
        ticket_id=42,
        reply_type="solution",
        body_with_footer="A customer reply",
        internal_analysis="Confirmed from logs.",
    )
    caller = AsyncMock(
        return_value={
            "success": True,
            "data": {"comments": [{"body": note}]},
        },
    )

    result = await ensure_post_public_internal_note(
        ticket_id=42,
        reply_type="solution",
        body_with_footer="A customer reply",
        internal_analysis="Confirmed from logs.",
        zendesk_tool_caller=caller,
    )

    assert result is True
    caller.assert_awaited_once()
    assert caller.await_args.args[0] == "get_ticket_comments"


@pytest.mark.asyncio
async def test_transient_internal_note_failure_is_retried() -> None:
    caller = AsyncMock(
        side_effect=[
            {"success": True, "data": {"comments": []}},
            {"success": False, "error": "temporary failure"},
            {"success": True, "data": {"comments": []}},
            {"success": True, "data": {"comments": []}},
            {"success": True, "data": {}},
        ],
    )

    result = await ensure_post_public_internal_note(
        ticket_id=42,
        reply_type="ask",
        body_with_footer="Please share the error message.",
        internal_analysis="The exact error is still unknown.",
        zendesk_tool_caller=caller,
    )

    assert result is True
    assert [call.args[0] for call in caller.await_args_list].count(
        "add_internal_note",
    ) == 2
