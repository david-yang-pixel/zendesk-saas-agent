"""RED contracts for crash-safe public-reply follow-up delivery.

These scenarios exercise process boundaries that ordinary mocked tool tests do
not cover:

* a public reply can reach Zendesk before the trigger-state callback runs;
* a retry can regenerate different prose while the outbox still owns body A;
* the optional post-public internal note can fail after the public send when enabled.

All three side effects must remain tied to one durable logical delivery attempt.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from zd_agent.config import AppConfig, PublicReplyConfig
from zd_agent.runtime.pre_send_check import FreshnessResult
from zd_agent.services import public_reply_internal_note as internal_note_module
from zd_agent.services.app_bootstrap import prepare_saas_app_runtime
from zd_agent.services.public_reply_delivery_store import (
    PublicReplyDeliveryStateError,
    PublicReplyDeliveryStore,
)
from zd_agent.services.public_reply_handoff_effects import (
    PostPublicHandoffStore,
    reconcile_pending_post_public_handoff_effects,
)
from zd_agent.services.public_reply_internal_note import (
    PostPublicInternalNoteStore,
    build_post_public_internal_note,
    ensure_post_public_internal_note,
    reconcile_pending_post_public_internal_notes,
)
from zd_agent.services.reliable_public_reply import (
    send_casework_public_reply_with_freshness,
)
from zd_agent.tools.public_reply_delivery import send_public_reply_safely
from zd_agent.tools.public_reply_format import _append_footer
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


TICKET_ID = 3461
ORIGINAL_DELIVERY_ATTEMPT_ID = "messaging:3461:form-response-m2"


def _insert_legacy_delivery(
    path: Path,
    *,
    delivery_attempt_id: str,
    reply_type: str,
    delivered: bool,
    ambiguous: bool = False,
) -> tuple[str, str]:
    store = PublicReplyDeliveryStore(path)
    del store
    delivery_key = hashlib.sha256(
        (f"v1\0{delivery_attempt_id}\0{TICKET_ID}\0{reply_type}").encode("utf-8")
    ).hexdigest()
    body = _append_footer(f"Frozen legacy {reply_type} reply.", reply_type)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO public_reply_deliveries("
            "delivery_key,reply_type,body,internal_analysis,created_at,"
            "delivered_at,ambiguous_at) VALUES (?,?,?,?,?,?,?)",
            (
                delivery_key,
                reply_type,
                body,
                "Frozen legacy analysis.",
                100.0,
                101.0 if delivered else None,
                100.5 if ambiguous else None,
            ),
        )
    return delivery_key, body


def test_public_delivery_outbox_migrates_legacy_rows_with_ambiguous_flag(
    tmp_path,
) -> None:
    path = tmp_path / "legacy-public.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE public_reply_deliveries ("
            "delivery_key TEXT PRIMARY KEY,reply_type TEXT NOT NULL DEFAULT '',"
            "body TEXT NOT NULL,internal_analysis TEXT NOT NULL DEFAULT '',"
            "snapshot_customer_message_count INTEGER,"
            "snapshot_customer_last_message_id TEXT NOT NULL DEFAULT '',"
            "migrated_from_v1 INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,"
            "delivered_at REAL,claim_token TEXT NOT NULL DEFAULT '',"
            "lease_until REAL NOT NULL DEFAULT 0)"
        )

    store = PublicReplyDeliveryStore(path)
    attempt = store.prepare(
        ticket_id=TICKET_ID,
        delivery_attempt_id="legacy-ambiguous-column",
        reply_type="solution",
        body="Frozen body",
    )

    assert attempt.ambiguous is False


def test_public_delivery_store_normalizes_stale_delivered_ambiguous_row(
    tmp_path,
) -> None:
    path = tmp_path / "public_reply_outbox.sqlite3"
    store = PublicReplyDeliveryStore(path)
    attempt = store.prepare(
        delivery_attempt_id="delivered-with-stale-fence",
        ticket_id=TICKET_ID,
        reply_type="solution",
        body="Already delivered answer",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m1",
    )
    token = store.claim(attempt.delivery_key)
    assert token is not None
    assert store.begin_send(attempt.delivery_key, token)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE public_reply_deliveries SET delivered_at = ? "
            "WHERE delivery_key = ?",
            (101.0, attempt.delivery_key),
        )

    reopened = PublicReplyDeliveryStore(path)
    recovered = reopened.get(
        delivery_attempt_id="delivered-with-stale-fence",
        ticket_id=TICKET_ID,
    )

    assert recovered is not None
    assert recovered.delivered is True
    assert recovered.ambiguous is False


def _config(tmp_path) -> AppConfig:
    cfg = AppConfig(
        public_reply=PublicReplyConfig(
            enabled=True,
            internal_audit_note_enabled=True,
            browser_relay_url="https://relay.example.test/zendesk-reply-mcp",
            browser_relay_api_key="relay-secret",
            freshness_callback_url=(
                "https://relay.example.test/webhook/relay-pre-send-freshness"
            ),
        ),
    )
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    cfg.webhook.listen_port = 8080
    return cfg


def _prepare_durable_delivery_payload(
    *,
    cfg: AppConfig,
    ticket_id: int,
    reply_type: str,
    delivery_attempt_id: str,
    body_with_footer: str,
    internal_analysis: str,
) -> tuple[
    str,
    str,
    str,
    PublicReplyDeliveryStore,
    PostPublicInternalNoteStore,
]:
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    delivery_store = PublicReplyDeliveryStore(state_dir / "public_reply_outbox.sqlite3")
    attempt = delivery_store.prepare(
        delivery_attempt_id=delivery_attempt_id,
        ticket_id=ticket_id,
        reply_type=reply_type,
        body=body_with_footer,
        internal_analysis=internal_analysis,
    )
    note_store = PostPublicInternalNoteStore(
        delivery_store.path.parent / "post_public_internal_note_outbox.sqlite3"
    )
    note_store.prepare(
        ticket_id=ticket_id,
        delivery_attempt_id=delivery_attempt_id,
        reply_type=attempt.reply_type,
        body_with_footer=attempt.body,
        internal_analysis=attempt.internal_analysis,
        public_delivery_confirmed=attempt.delivered,
        accept_legacy_reference=attempt.migrated_from_v1,
    )
    return (
        attempt.body,
        attempt.internal_analysis,
        attempt.reply_type,
        delivery_store,
        note_store,
    )


def _internal_note_reference_line(
    *,
    body_with_footer: str,
    internal_analysis: str,
    delivery_attempt_id: str = "",
) -> str:
    note = build_post_public_internal_note(
        ticket_id=TICKET_ID,
        reply_type="solution",
        body_with_footer=body_with_footer,
        internal_analysis=internal_analysis,
        delivery_attempt_id=delivery_attempt_id,
    )
    return next(line for line in note.splitlines() if line.startswith("- Reference:"))


@pytest.mark.asyncio
async def test_startup_recovery_reuses_original_delivery_attempt_after_public_send(
    tmp_path,
) -> None:
    """A crash before the state callback must not create a second send key."""
    cfg = _config(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    tracker = TicketStateTracker(state_dir / "tickets.json")
    tracker.set(
        TICKET_ID,
        TicketState(
            state="processing",
            first_seen=datetime(2026, 7, 16, 4, 18, tzinfo=timezone.utc),
            active_session_id="session-before-crash",
            delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        ),
    )

    store = PublicReplyDeliveryStore(state_dir / "public_reply_outbox.sqlite3")
    relay = AsyncMock()
    zendesk = AsyncMock(
        return_value={"success": True, "data": {"comments": []}},
    )
    body_a = _append_footer("We are investigating this issue.", "handoff")

    first_send = await send_public_reply_safely(
        cfg=cfg,
        ticket_id=TICKET_ID,
        body_with_footer=body_a,
        reply_type="handoff",
        conversation_id="conversation-3461",
        snapshot_customer_message_count=None,
        snapshot_customer_last_message_id=None,
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        delivery_store=store,
        delivery_store_factory=lambda _cfg: store,
        zendesk_tool_caller=zendesk,
        relay_sender=relay,
    )
    assert first_send is True

    # Simulate a process restart before SessionManager's on_finished callback.
    # The durable trigger state is still ``processing``.
    runtime = prepare_saas_app_runtime(
        app=web.Application(),
        cfg=cfg,
        manager=MagicMock(),
    )
    recovered = await runtime.trigger_queue.get()

    assert recovered["source"] == "startup_recovery"
    assert recovered["delivery_attempt_id"] == ORIGINAL_DELIVERY_ATTEMPT_ID

    second_send = await send_public_reply_safely(
        cfg=cfg,
        ticket_id=TICKET_ID,
        body_with_footer=_append_footer(
            "This regenerated reply must not be sent.",
            "handoff",
        ),
        reply_type="handoff",
        conversation_id="conversation-3461",
        snapshot_customer_message_count=None,
        snapshot_customer_last_message_id=None,
        delivery_attempt_id=recovered["delivery_attempt_id"],
        delivery_store=store,
        delivery_store_factory=lambda _cfg: store,
        zendesk_tool_caller=zendesk,
        relay_sender=relay,
    )

    assert second_send is False
    assert relay.await_count == 1, "startup recovery must confirm, not resend"
    await runtime.trigger_queue.ack(recovered)


@pytest.mark.asyncio
async def test_retry_internal_note_references_outbox_body_not_regenerated_body(
    tmp_path,
) -> None:
    """If body A owns the attempt, a retry generated as B must record A."""
    cfg = _config(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    store = PublicReplyDeliveryStore(state_dir / "public_reply_outbox.sqlite3")
    relay = AsyncMock()
    no_remote_duplicate = AsyncMock(
        return_value={"success": True, "data": {"comments": []}},
    )
    body_a = _append_footer(
        "The stored answer is body A.",
        "solution",
    )
    body_b_text = "A restarted model regenerated body B."
    body_b = _append_footer(body_b_text, "solution")
    internal_analysis = "The retry reconciled a previously delivered reply."

    # Public delivery succeeds, then the process dies before writing its note.
    delivered = await send_public_reply_safely(
        cfg=cfg,
        ticket_id=TICKET_ID,
        body_with_footer=body_a,
        reply_type="solution",
        conversation_id="conversation-3461",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m2",
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        delivery_store=store,
        delivery_store_factory=lambda _cfg: store,
        zendesk_tool_caller=no_remote_duplicate,
        relay_sender=relay,
    )
    assert delivered is True

    note_bodies: list[str] = []

    async def zendesk_after_restart(name: str, arguments: dict[str, Any]) -> dict:
        if name == "get_ticket_comments":
            return {"success": True, "data": {"comments": []}}
        if name == "add_internal_note":
            note_bodies.append(str(arguments["body"]))
            return {"success": True, "data": {"comment_id": 9001}}
        raise AssertionError(f"unexpected Zendesk tool: {name}")

    with (
        patch(
            "zd_agent.services.reliable_public_reply.get_config",
            return_value=cfg,
        ),
        patch(
            "zd_agent.services.reliable_public_reply._delivery_store_for_config",
            return_value=store,
        ),
        patch(
            "zd_agent.services.reliable_public_reply.call_zendesk_tool",
            side_effect=zendesk_after_restart,
        ),
        patch(
            "zd_agent.services.reliable_public_reply.send_browser_relay_reply",
            relay,
        ),
        patch(
            "zd_agent.services.reliable_public_reply._check_freshness_before_relay",
            AsyncMock(
                return_value=FreshnessResult(
                    continue_send=True,
                    reason="fresh",
                )
            ),
        ),
    ):
        result = await send_casework_public_reply_with_freshness(
            ticket_id=TICKET_ID,
            body=body_b_text,
            reply_type="solution",
            conversation_id="conversation-3461",
            snapshot_customer_message_count=1,
            snapshot_customer_last_message_id="m2",
            delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
            internal_analysis=internal_analysis,
        )

    assert result.success is True
    assert relay.await_count == 1, "the retry must not send regenerated body B"
    assert len(note_bodies) == 1
    assert (
        _internal_note_reference_line(
            body_with_footer=body_a,
            internal_analysis=internal_analysis,
            delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        )
        in note_bodies[0]
    )
    assert (
        _internal_note_reference_line(
            body_with_footer=body_b,
            internal_analysis=internal_analysis,
            delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        )
        not in note_bodies[0]
    )


@pytest.mark.asyncio
async def test_definitely_unsent_internal_note_is_reconciled_once_after_restart(
    tmp_path,
) -> None:
    """An explicit pre-send rejection remains retryable after credentials recover."""
    store_type = getattr(
        internal_note_module,
        "PostPublicInternalNoteStore",
        None,
    )
    reconcile = getattr(
        internal_note_module,
        "reconcile_pending_post_public_internal_notes",
        None,
    )
    assert store_type is not None, (
        "post-public internal notes need a durable store, not only in-process retries"
    )
    assert reconcile is not None, (
        "startup/background recovery must reconcile durable internal-note work"
    )

    store_path = tmp_path / "post-public-internal-note-outbox.sqlite3"
    note_store = store_type(store_path)

    async def unavailable_zendesk(name: str, _arguments: dict[str, Any]) -> dict:
        if name == "get_ticket_comments":
            return {"success": True, "data": {"comments": []}}
        if name == "add_internal_note":
            return {
                "success": False,
                "code": "UNAUTHORIZED",
                "error": "credentials unavailable",
            }
        raise AssertionError(f"unexpected Zendesk tool: {name}")

    delivered = await ensure_post_public_internal_note(
        ticket_id=TICKET_ID,
        reply_type="solution",
        body_with_footer=_append_footer("The customer received this.", "solution"),
        internal_analysis="The public reply succeeded before the MCP outage.",
        zendesk_tool_caller=unavailable_zendesk,
        note_store=note_store,
    )
    assert delivered is False

    # Reopen from disk to prove the pending work was not process-local.
    recovered_store = store_type(store_path)
    remote_comments: list[dict[str, Any]] = []
    recovered_add_calls = 0

    async def recovered_zendesk(name: str, arguments: dict[str, Any]) -> dict:
        nonlocal recovered_add_calls
        if name == "get_ticket_comments":
            return {
                "success": True,
                "data": {"comments": list(remote_comments)},
            }
        if name == "add_internal_note":
            recovered_add_calls += 1
            remote_comments.append({"body": str(arguments["body"])})
            return {"success": True, "data": {"comment_id": 9002}}
        raise AssertionError(f"unexpected Zendesk tool: {name}")

    await reconcile(
        note_store=recovered_store,
        zendesk_tool_caller=recovered_zendesk,
    )
    await reconcile(
        note_store=store_type(store_path),
        zendesk_tool_caller=recovered_zendesk,
    )

    assert recovered_add_calls == 1
    assert len(remote_comments) == 1


@pytest.mark.asyncio
async def test_retry_cannot_change_reply_type_for_same_logical_attempt(
    tmp_path,
) -> None:
    """A regenerated reply type must not create a second customer delivery."""
    cfg = _config(tmp_path)
    store = PublicReplyDeliveryStore(tmp_path / "public_reply_outbox.sqlite3")
    relay = AsyncMock()
    zendesk = AsyncMock(
        return_value={"success": True, "data": {"comments": []}},
    )

    first = await send_public_reply_safely(
        cfg=cfg,
        ticket_id=TICKET_ID,
        body_with_footer=_append_footer("A human will follow up.", "handoff"),
        reply_type="handoff",
        conversation_id="conversation-3461",
        snapshot_customer_message_count=None,
        snapshot_customer_last_message_id=None,
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        delivery_store=store,
        delivery_store_factory=lambda _cfg: store,
        zendesk_tool_caller=zendesk,
        relay_sender=relay,
    )
    second = await send_public_reply_safely(
        cfg=cfg,
        ticket_id=TICKET_ID,
        body_with_footer=_append_footer("A regenerated solution.", "solution"),
        reply_type="solution",
        conversation_id="conversation-3461",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m2",
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        delivery_store=store,
        delivery_store_factory=lambda _cfg: store,
        zendesk_tool_caller=zendesk,
        relay_sender=relay,
    )

    assert first is True
    assert second is False
    assert relay.await_count == 1


@pytest.mark.asyncio
async def test_identical_public_bodies_get_one_note_per_delivery_attempt(
    tmp_path,
) -> None:
    """Reference dedupe is per logical send, not merely per rendered body."""
    store_type = internal_note_module.PostPublicInternalNoteStore
    note_store = store_type(tmp_path / "post-public-notes.sqlite3")
    remote_comments: list[dict[str, Any]] = []

    async def zendesk(name: str, arguments: dict[str, Any]) -> dict:
        if name == "get_ticket_comments":
            return {"success": True, "data": {"comments": remote_comments}}
        if name == "add_internal_note":
            remote_comments.append({"body": str(arguments["body"])})
            return {"success": True, "data": {"comment_id": len(remote_comments)}}
        raise AssertionError(name)

    common = {
        "ticket_id": TICKET_ID,
        "reply_type": "solution",
        "body_with_footer": _append_footer("Same safe answer.", "solution"),
        "internal_analysis": "Evidence was rechecked for this interaction.",
        "zendesk_tool_caller": zendesk,
        "note_store": note_store,
    }
    assert await ensure_post_public_internal_note(
        **common,
        delivery_attempt_id="customer-message-1",
    )
    assert await ensure_post_public_internal_note(
        **common,
        delivery_attempt_id="customer-message-2",
    )

    assert len(remote_comments) == 2
    references = {
        line
        for comment in remote_comments
        for line in str(comment["body"]).splitlines()
        if line.startswith("- Reference:")
    }
    assert len(references) == 2


def test_legacy_v1_outbox_row_migrates_to_type_independent_attempt(tmp_path) -> None:
    """An already-delivered v1 row must protect the v2 logical attempt."""
    path = tmp_path / "public_reply_outbox.sqlite3"
    legacy_key = hashlib.sha256(
        (f"v1\0{ORIGINAL_DELIVERY_ATTEMPT_ID}\0{TICKET_ID}\0handoff").encode("utf-8")
    ).hexdigest()
    body_a = _append_footer("A human will follow up.", "handoff")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE public_reply_deliveries ("
            "delivery_key TEXT PRIMARY KEY,body TEXT NOT NULL,"
            "created_at REAL NOT NULL,delivered_at REAL,"
            "claim_token TEXT NOT NULL DEFAULT '',"
            "lease_until REAL NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "INSERT INTO public_reply_deliveries("
            "delivery_key,body,created_at,delivered_at) VALUES (?,?,?,?)",
            (legacy_key, body_a, 100.0, 101.0),
        )

    attempt = PublicReplyDeliveryStore(path).prepare(
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        ticket_id=TICKET_ID,
        reply_type="solution",
        body=_append_footer("Regenerated solution.", "solution"),
    )

    assert attempt.delivered is True
    assert attempt.body == body_a
    assert attempt.reply_type == "handoff"


def test_multiple_ambiguous_v1_rows_fail_closed_instead_of_deleting_evidence(
    tmp_path,
) -> None:
    path = tmp_path / "public_reply_outbox.sqlite3"
    store = PublicReplyDeliveryStore(path)
    with sqlite3.connect(path) as connection:
        for reply_type, body, created_at in (
            ("ask", "First ambiguous body", 100.0),
            ("solution", "Second ambiguous body", 101.0),
        ):
            legacy_key = hashlib.sha256(
                (
                    f"v1\0{ORIGINAL_DELIVERY_ATTEMPT_ID}\0{TICKET_ID}\0{reply_type}"
                ).encode("utf-8")
            ).hexdigest()
            connection.execute(
                "INSERT INTO public_reply_deliveries("
                "delivery_key,reply_type,body,internal_analysis,created_at) "
                "VALUES (?,?,?,?,?)",
                (legacy_key, reply_type, body, "", created_at),
            )

    from zd_agent.services.public_reply_delivery_store import (
        PublicReplyDeliveryStateError,
    )

    with pytest.raises(PublicReplyDeliveryStateError, match="multiple legacy"):
        store.prepare(
            delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
            ticket_id=TICKET_ID,
            reply_type="handoff",
            body="Regenerated body",
        )

    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM public_reply_deliveries"
            ).fetchone()[0]
            == 2
        )


def test_mixed_v2_pending_and_v1_delivery_fails_closed(tmp_path) -> None:
    path = tmp_path / "public_reply_outbox.sqlite3"
    store = PublicReplyDeliveryStore(path)
    store.prepare(
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        ticket_id=TICKET_ID,
        reply_type="solution",
        body="Pending v2 body",
    )
    legacy_key = hashlib.sha256(
        (f"v1\0{ORIGINAL_DELIVERY_ATTEMPT_ID}\0{TICKET_ID}\0handoff").encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO public_reply_deliveries("
            "delivery_key,reply_type,body,internal_analysis,created_at,delivered_at) "
            "VALUES (?,?,?,?,?,?)",
            (legacy_key, "handoff", "Delivered v1 body", "", 100.0, 101.0),
        )

    from zd_agent.services.public_reply_delivery_store import (
        PublicReplyDeliveryStateError,
    )

    with pytest.raises(PublicReplyDeliveryStateError, match="mixed v1/v2"):
        store.prepare(
            delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
            ticket_id=TICKET_ID,
            reply_type="solution",
            body="Regenerated v2 body",
        )


@pytest.mark.asyncio
async def test_note_intent_exists_before_send_and_activates_from_delivery_outbox(
    tmp_path,
) -> None:
    cfg = _config(tmp_path)
    body = _append_footer("We confirmed the next step.", "solution")
    (
        frozen_body,
        frozen_analysis,
        frozen_reply_type,
        delivery_store,
        note_store,
    ) = _prepare_durable_delivery_payload(
        cfg=cfg,
        ticket_id=TICKET_ID,
        reply_type="solution",
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        body_with_footer=body,
        internal_analysis="The public reply is evidence-backed.",
    )
    assert isinstance(note_store, PostPublicInternalNoteStore)
    pending_before_send = note_store.pending()
    assert len(pending_before_send) == 1
    assert pending_before_send[0].public_delivery_confirmed is False

    await send_public_reply_safely(
        cfg=cfg,
        ticket_id=TICKET_ID,
        body_with_footer=frozen_body,
        reply_type=frozen_reply_type,
        conversation_id="conversation-3461",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m2",
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        delivery_store=delivery_store,
        delivery_store_factory=lambda _cfg: delivery_store,
        zendesk_tool_caller=AsyncMock(
            return_value={"success": True, "data": {"comments": []}},
        ),
        relay_sender=AsyncMock(),
        internal_analysis=frozen_analysis,
    )

    remote_notes: list[str] = []

    async def recovered_zendesk(name: str, arguments: dict[str, Any]) -> dict:
        if name == "get_ticket_comments":
            return {"success": True, "data": {"comments": []}}
        remote_notes.append(str(arguments["body"]))
        return {"success": True, "data": {}}

    reconciled = await reconcile_pending_post_public_internal_notes(
        note_store=PostPublicInternalNoteStore(note_store.path),
        delivery_store=PublicReplyDeliveryStore(delivery_store.path),
        zendesk_tool_caller=recovered_zendesk,
    )

    assert reconciled == 1
    assert len(remote_notes) == 1


@pytest.mark.asyncio
async def test_migrated_v1_delivery_recognizes_existing_v1_internal_note(
    tmp_path,
) -> None:
    cfg = _config(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    path = state_dir / "public_reply_outbox.sqlite3"
    legacy_body = _append_footer("A human will follow up.", "handoff")
    legacy_key = hashlib.sha256(
        (f"v1\0{ORIGINAL_DELIVERY_ATTEMPT_ID}\0{TICKET_ID}\0handoff").encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE public_reply_deliveries ("
            "delivery_key TEXT PRIMARY KEY,body TEXT NOT NULL,"
            "created_at REAL NOT NULL,delivered_at REAL)"
        )
        connection.execute(
            "INSERT INTO public_reply_deliveries("
            "delivery_key,body,created_at,delivered_at) VALUES (?,?,?,?)",
            (legacy_key, legacy_body, 100.0, 101.0),
        )

    (
        frozen_body,
        frozen_analysis,
        frozen_reply_type,
        _delivery_store,
        note_store,
    ) = _prepare_durable_delivery_payload(
        cfg=cfg,
        ticket_id=TICKET_ID,
        reply_type="solution",
        delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
        body_with_footer="Regenerated body must not replace the delivered handoff.",
        internal_analysis="Recovered legacy delivery.",
    )
    pending = note_store.pending()[0]
    assert pending.accept_legacy_reference is True
    legacy_note = build_post_public_internal_note(
        ticket_id=TICKET_ID,
        reply_type=frozen_reply_type,
        body_with_footer=frozen_body,
        internal_analysis=frozen_analysis,
    )
    caller = AsyncMock(
        return_value={"success": True, "data": {"comments": [{"body": legacy_note}]}},
    )

    assert (
        await ensure_post_public_internal_note(
            ticket_id=TICKET_ID,
            delivery_attempt_id=ORIGINAL_DELIVERY_ATTEMPT_ID,
            reply_type=frozen_reply_type,
            body_with_footer=frozen_body,
            internal_analysis=frozen_analysis,
            zendesk_tool_caller=caller,
            note_store=note_store,
            public_delivery_confirmed=True,
        )
        is True
    )
    assert [call.args[0] for call in caller.await_args_list] == ["get_ticket_comments"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_type", ["ask", "solution"])
async def test_cold_start_reconciles_confirmed_v1_internal_note_intent(
    reply_type: str,
    tmp_path,
) -> None:
    delivery_path = tmp_path / "public.sqlite3"
    attempt_id = f"cold-start-{reply_type}"
    _legacy_key, body = _insert_legacy_delivery(
        delivery_path,
        delivery_attempt_id=attempt_id,
        reply_type=reply_type,
        delivered=True,
    )
    note_store = PostPublicInternalNoteStore(tmp_path / "notes.sqlite3")
    note_store.prepare(
        ticket_id=TICKET_ID,
        delivery_attempt_id=attempt_id,
        reply_type=reply_type,
        body_with_footer=body,
        internal_analysis="Frozen legacy analysis.",
        public_delivery_confirmed=False,
    )
    caller = AsyncMock(
        side_effect=[
            {"success": True, "data": {"comments": []}},
            {"success": True, "data": {"comment_id": 9001}},
        ]
    )

    reconciled = await reconcile_pending_post_public_internal_notes(
        note_store=note_store,
        delivery_store=PublicReplyDeliveryStore(delivery_path),
        zendesk_tool_caller=caller,
    )

    assert reconciled == 1
    assert note_store.pending_count() == 0
    assert [call.args[0] for call in caller.await_args_list] == [
        "get_ticket_comments",
        "add_internal_note",
    ]


@pytest.mark.asyncio
async def test_cold_start_reconciles_confirmed_v1_handoff_intent(
    tmp_path,
) -> None:
    delivery_path = tmp_path / "public.sqlite3"
    attempt_id = "cold-start-handoff"
    _legacy_key, body = _insert_legacy_delivery(
        delivery_path,
        delivery_attempt_id=attempt_id,
        reply_type="handoff",
        delivered=True,
    )
    handoff_store = PostPublicHandoffStore(tmp_path / "handoff.sqlite3")
    handoff_store.prepare(
        ticket_id=TICKET_ID,
        delivery_attempt_id=attempt_id,
        body_with_footer=body,
        public_delivery_confirmed=False,
    )
    feishu = AsyncMock(return_value=True)
    tag = AsyncMock(return_value=True)

    reconciled = await reconcile_pending_post_public_handoff_effects(
        store=handoff_store,
        delivery_store=PublicReplyDeliveryStore(delivery_path),
        feishu_sender=feishu,
        tag_sender=tag,
    )

    assert reconciled == 1
    feishu.assert_awaited_once()
    tag.assert_awaited_once()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_cold_start_quarantines_unconfirmed_v1_delivery(
    ambiguous: bool,
    tmp_path,
) -> None:
    delivery_path = tmp_path / "public.sqlite3"
    attempt_id = f"cold-start-unconfirmed-{ambiguous}"
    legacy_key, _body = _insert_legacy_delivery(
        delivery_path,
        delivery_attempt_id=attempt_id,
        reply_type="solution",
        delivered=False,
        ambiguous=ambiguous,
    )
    store = PublicReplyDeliveryStore(delivery_path)

    assert (
        store.get(
            delivery_attempt_id=attempt_id,
            ticket_id=TICKET_ID,
        )
        is None
    )
    with sqlite3.connect(delivery_path) as connection:
        rows = connection.execute(
            "SELECT delivery_key,migrated_from_v1 FROM public_reply_deliveries"
        ).fetchall()
    assert rows == [(legacy_key, 0)]


def test_cold_start_legacy_lookup_fails_closed_for_multiple_or_mixed_rows(
    tmp_path,
) -> None:
    multiple_path = tmp_path / "multiple.sqlite3"
    attempt_id = "cold-start-multiple"
    for reply_type in ("ask", "solution"):
        _insert_legacy_delivery(
            multiple_path,
            delivery_attempt_id=attempt_id,
            reply_type=reply_type,
            delivered=True,
        )
    with pytest.raises(PublicReplyDeliveryStateError, match="multiple legacy"):
        PublicReplyDeliveryStore(multiple_path).get(
            delivery_attempt_id=attempt_id,
            ticket_id=TICKET_ID,
        )

    mixed_path = tmp_path / "mixed.sqlite3"
    mixed_store = PublicReplyDeliveryStore(mixed_path)
    mixed_store.prepare(
        delivery_attempt_id="cold-start-mixed",
        ticket_id=TICKET_ID,
        reply_type="solution",
        body="Frozen v2 body",
    )
    _insert_legacy_delivery(
        mixed_path,
        delivery_attempt_id="cold-start-mixed",
        reply_type="handoff",
        delivered=True,
    )
    with pytest.raises(PublicReplyDeliveryStateError, match="mixed v1/v2"):
        mixed_store.get(
            delivery_attempt_id="cold-start-mixed",
            ticket_id=TICKET_ID,
        )
