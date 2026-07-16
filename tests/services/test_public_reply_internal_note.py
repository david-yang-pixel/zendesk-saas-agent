from __future__ import annotations

import asyncio
from hashlib import sha256
import sqlite3
from unittest.mock import AsyncMock

import pytest

from zd_agent.services.public_reply_internal_note import (
    build_post_public_internal_note,
    ensure_post_public_internal_note,
)


def test_legacy_reference_hash_remains_compatible_with_existing_notes() -> None:
    body = "A customer reply"
    note = build_post_public_internal_note(
        ticket_id=42,
        reply_type="solution",
        body_with_footer=body,
        internal_analysis="Confirmed from logs.",
    )
    expected = sha256(f"42\0{body}".encode()).hexdigest()[:16]

    assert f"public-reply-analysis:{expected}" in note


def test_pre_confirmation_schema_rows_migrate_as_already_delivered(tmp_path) -> None:
    path = tmp_path / "notes.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE post_public_internal_notes ("
            "reference TEXT PRIMARY KEY,ticket_id INTEGER NOT NULL,"
            "reply_type TEXT NOT NULL,body_with_footer TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO post_public_internal_notes("
            "reference,ticket_id,reply_type,body_with_footer) VALUES (?,?,?,?)",
            ("legacy-ref", 42, "solution", "Already delivered public reply"),
        )

    from zd_agent.services.public_reply_internal_note import (
        PostPublicInternalNoteStore,
    )

    pending = PostPublicInternalNoteStore(path).pending()
    assert len(pending) == 1
    assert pending[0].public_delivery_confirmed is True


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
async def test_ambiguous_internal_note_failure_is_not_retried_without_proof() -> None:
    caller = AsyncMock(
        side_effect=[
            {"success": True, "data": {"comments": []}},
            {"success": False, "error": "temporary failure"},
            {"success": False, "error": "verification unavailable"},
        ],
    )

    result = await ensure_post_public_internal_note(
        ticket_id=42,
        reply_type="ask",
        body_with_footer="Please share the error message.",
        internal_analysis="The exact error is still unknown.",
        zendesk_tool_caller=caller,
    )

    assert result is False
    assert [call.args[0] for call in caller.await_args_list].count(
        "add_internal_note",
    ) == 1


@pytest.mark.asyncio
async def test_ambiguous_note_write_is_never_reissued_after_restart(tmp_path) -> None:
    """A lost write response leaves an operator-reconcile row, not a duplicate."""
    from zd_agent.services.public_reply_internal_note import (
        PostPublicInternalNoteStore,
        reconcile_pending_post_public_internal_notes,
    )

    path = tmp_path / "notes.sqlite3"
    first = AsyncMock(
        side_effect=[
            {"success": True, "data": {"comments": []}},
            TimeoutError("response lost after write"),
            {"success": False, "error": "comments temporarily unavailable"},
        ]
    )
    assert not await ensure_post_public_internal_note(
        ticket_id=42,
        delivery_attempt_id="attempt-42",
        reply_type="solution",
        body_with_footer="Customer-visible answer.",
        internal_analysis="Evidence-backed answer.",
        zendesk_tool_caller=first,
        note_store=PostPublicInternalNoteStore(path),
    )

    retry = AsyncMock(return_value={"success": True, "data": {"comments": []}})
    assert await reconcile_pending_post_public_internal_notes(
        note_store=PostPublicInternalNoteStore(path),
        zendesk_tool_caller=retry,
    ) == 0
    assert [call.args[0] for call in retry.await_args_list] == [
        "get_ticket_comments",
    ]


@pytest.mark.asyncio
async def test_unavailable_duplicate_check_never_attempts_internal_note_write() -> None:
    caller = AsyncMock(
        return_value={"success": False, "error": "Zendesk unavailable"},
    )

    result = await ensure_post_public_internal_note(
        ticket_id=42,
        reply_type="solution",
        body_with_footer="Customer-visible answer.",
        internal_analysis="Evidence-backed answer.",
        zendesk_tool_caller=caller,
    )

    assert result is False
    assert [call.args[0] for call in caller.await_args_list] == [
        "get_ticket_comments",
    ]


@pytest.mark.asyncio
async def test_active_internal_note_claim_is_renewed_during_slow_remote_call(
    tmp_path,
) -> None:
    from zd_agent.services.public_reply_internal_note import (
        PostPublicInternalNoteStore,
    )

    store = PostPublicInternalNoteStore(
        tmp_path / "notes.sqlite3",
        claim_lease_seconds=1,
    )
    first_lookup_started = asyncio.Event()
    remote_notes: list[str] = []

    async def first_caller(name: str, arguments: dict) -> dict:
        if name == "get_ticket_comments":
            first_lookup_started.set()
            await asyncio.sleep(1.2)
            return {"success": True, "data": {"comments": []}}
        remote_notes.append(str(arguments["body"]))
        return {"success": True, "data": {}}

    async def second_caller(name: str, arguments: dict) -> dict:
        if name == "get_ticket_comments":
            return {"success": True, "data": {"comments": []}}
        remote_notes.append(str(arguments["body"]))
        return {"success": True, "data": {}}

    first = asyncio.create_task(
        ensure_post_public_internal_note(
            ticket_id=42,
            reply_type="handoff",
            body_with_footer="A human will follow up.",
            internal_analysis="Manual review is required.",
            zendesk_tool_caller=first_caller,
            note_store=store,
            delivery_attempt_id="attempt-42",
        )
    )
    await first_lookup_started.wait()
    await asyncio.sleep(1.05)
    second = await ensure_post_public_internal_note(
        ticket_id=42,
        reply_type="handoff",
        body_with_footer="A human will follow up.",
        internal_analysis="Manual review is required.",
        zendesk_tool_caller=second_caller,
        note_store=store,
        delivery_attempt_id="attempt-42",
    )

    assert second is False
    assert await first is True
    assert len(remote_notes) == 1
