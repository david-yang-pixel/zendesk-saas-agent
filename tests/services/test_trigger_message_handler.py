"""Tests for single trigger message handling orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, IntakeConfig, PublicReplyConfig, ZendeskConfig
from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.mention.tracker import MentionTracker
from zd_agent.services.direct_dispatch import DirectDispatchResult
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.services.trigger_message_handler import handle_trigger_message
from zd_agent.session.agent_session import AgentSession
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.casework.mode = ExecutionMode.CASEWORK
    cfg.public_reply = PublicReplyConfig(enabled=False)
    cfg.zendesk = ZendeskConfig(
        subdomain="x",
        email="x@y.com",
        api_token="t",
        min_ticket_id=0,
        agent_user_id=45433304089364,
    )
    cfg.intake = IntakeConfig(
        post_agent_cooldown_seconds=180,
    )
    return cfg


def _dispatching_manager() -> MagicMock:
    manager = MagicMock()

    async def dispatch(ticket_id: int, **kwargs):
        session = AgentSession(ticket_id=ticket_id, session_id="session-created")
        kwargs["on_session_created"](session)
        return session

    manager.dispatch = AsyncMock(side_effect=dispatch)
    return manager


@pytest.mark.asyncio
async def test_direct_dispatch_message_is_handled_without_fetching(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = _dispatching_manager()
    fetch_messages = AsyncMock()

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "mention", "hint": "check"},
        manager=manager,
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_messages,
    )

    assert result.handled is True
    assert result.action == "direct_dispatch"
    manager.dispatch.assert_awaited_once()
    fetch_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_alias_mention_candidate_materializes_and_dispatches(
    tmp_path,
) -> None:
    cfg = _cfg()
    cfg.zendesk.mention_trigger = "@saas-agent"
    cfg.zendesk.mention_aliases = ["@yonggang"]
    tracker = MentionTracker(tmp_path / "processed_mentions.json")
    manager = _dispatching_manager()
    zendesk_call = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "comments": [
                    {
                        "id": 48640250132756,
                        "public": False,
                        "body": "@yonggang please investigate this ticket",
                        "author_id": 1001,
                        "via": {"channel": "web"},
                    }
                ],
                "next_page": None,
            },
        }
    )
    msg = {
        "ticket_id": 12345,
        "source": "mention_candidate",
        "comment_id": "48640250132756",
        "_queue_key": "mention:48640250132756",
    }

    with patch(
        "zd_agent.services.trigger_message_handler.call_zendesk_tool",
        zendesk_call,
        create=True,
    ):
        result = await handle_trigger_message(
            msg=msg,
            manager=manager,
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            mention_tracker=tracker,
        )

    assert result.action == "direct_dispatch"
    assert result.source == "mention"
    assert msg["source"] == "mention_candidate"
    assert "hint" not in msg
    assert tracker.is_processed("48640250132756") is True
    zendesk_call.assert_awaited_once_with(
        "get_ticket_comments",
        {"ticket_id": 12345, "page": 1, "per_page": 100},
    )
    manager.dispatch.assert_awaited_once()
    assert manager.dispatch.await_args.kwargs["hint"] == (
        "[来自 @mention 的请求]\n@yonggang please investigate this ticket"
    )


@pytest.mark.parametrize("action", ["stale_generation", "coalesced_active_session"])
@pytest.mark.asyncio
async def test_uncommitted_mention_dispatch_defers_without_marking(
    tmp_path,
    action: str,
) -> None:
    cfg = _cfg()
    cfg.zendesk.mention_trigger = "@saas-agent"
    tracker = MentionTracker(tmp_path / "processed_mentions.json")
    comment_id = f"comment-{action}"
    zendesk_call = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "comments": [
                    {
                        "id": comment_id,
                        "public": False,
                        "body": "@saas-agent retry this request",
                        "author_id": 1001,
                        "via": {"channel": "web"},
                    },
                ],
                "next_page": None,
            },
        },
    )

    with (
        patch(
            "zd_agent.services.trigger_message_handler.call_zendesk_tool",
            zendesk_call,
            create=True,
        ),
        patch(
            "zd_agent.services.trigger_message_handler.handle_direct_dispatch_source",
            AsyncMock(
                return_value=DirectDispatchResult(
                    handled=True,
                    dispatched=False,
                    action=action,
                ),
            ),
        ),
        pytest.raises(TriggerDeferredError) as exc_info,
    ):
        await handle_trigger_message(
            msg={
                "ticket_id": 12345,
                "source": "mention_candidate",
                "comment_id": comment_id,
                "_queue_key": f"mention:{comment_id}",
            },
            manager=MagicMock(),
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            mention_tracker=tracker,
        )

    assert exc_info.value.reason == "mention_dispatch_not_committed"
    assert tracker.is_processed(comment_id) is False


@pytest.mark.asyncio
async def test_processed_mention_candidate_is_skipped_before_refetch(tmp_path) -> None:
    cfg = _cfg()
    cfg.zendesk.mention_trigger = "@saas-agent"
    tracker = MentionTracker(tmp_path / "processed_mentions.json")
    tracker.mark_processed("same-comment")
    manager = _dispatching_manager()
    zendesk_call = AsyncMock()

    with patch(
        "zd_agent.services.trigger_message_handler.call_zendesk_tool",
        zendesk_call,
        create=True,
    ):
        result = await handle_trigger_message(
            msg={
                "ticket_id": 12345,
                "source": "mention_candidate",
                "comment_id": "same-comment",
                "_queue_key": "mention:same-comment",
            },
            manager=manager,
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            mention_tracker=tracker,
        )

    assert result.action == "mention_already_processed"
    zendesk_call.assert_not_awaited()
    manager.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_processed_raw_poller_mention_is_skipped_before_dispatch(
    tmp_path,
) -> None:
    tracker = MentionTracker(tmp_path / "processed_mentions.json")
    tracker.mark_processed("same-comment")
    manager = MagicMock(dispatch=AsyncMock())

    result = await handle_trigger_message(
        msg={
            "ticket_id": 12345,
            "source": "mention",
            "comment_id": "same-comment",
            "hint": "must not run twice",
        },
        manager=manager,
        state=TicketStateTracker(tmp_path / "tickets.json"),
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        mention_tracker=tracker,
    )

    assert result.action == "mention_already_processed"
    manager.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_mention_candidate_reads_the_newest_page_of_a_long_ticket(
    tmp_path,
) -> None:
    cfg = _cfg()
    cfg.zendesk.mention_trigger = "@saas-agent"
    tracker = MentionTracker(tmp_path / "processed_mentions.json")
    manager = _dispatching_manager()
    candidate_id = "latest-comment"

    async def zendesk_call(_name: str, arguments: dict) -> dict:
        page = arguments["page"]
        if page == 1:
            return {
                "success": True,
                "data": {
                    "comments": [{"id": f"old-{i}"} for i in range(100)],
                    "count": 1100,
                    "next_page": "page-2",
                },
            }
        if page == 11:
            return {
                "success": True,
                "data": {
                    "comments": [
                        {
                            "id": candidate_id,
                            "public": False,
                            "body": "@saas-agent inspect the latest update",
                            "author_id": 1001,
                            "via": {"channel": "web"},
                        }
                    ],
                    "count": 1100,
                    "next_page": None,
                },
            }
        raise AssertionError(f"unexpected page {page}")

    with patch(
        "zd_agent.services.trigger_message_handler.call_zendesk_tool",
        side_effect=zendesk_call,
        create=True,
    ) as caller:
        result = await handle_trigger_message(
            msg={
                "ticket_id": 12345,
                "source": "mention_candidate",
                "comment_id": candidate_id,
                "_queue_key": f"mention:{candidate_id}",
            },
            manager=manager,
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            mention_tracker=tracker,
        )

    assert result.action == "direct_dispatch"
    assert [call.args[1]["page"] for call in caller.await_args_list] == [1, 11]
    manager.dispatch.assert_awaited_once()


@pytest.mark.parametrize(
    "comment",
    [
        {
            "id": "public-1",
            "public": True,
            "body": "@saas-agent public reply",
            "author_id": 1001,
            "via": {"channel": "web"},
        },
        {
            "id": "plain-1",
            "public": False,
            "body": "ordinary internal note",
            "author_id": 1001,
            "via": {"channel": "web"},
        },
    ],
    ids=["public", "nonmention"],
)
@pytest.mark.asyncio
async def test_invalid_mention_candidate_is_marked_and_safely_skipped(
    tmp_path,
    comment: dict,
) -> None:
    cfg = _cfg()
    cfg.zendesk.mention_trigger = "@saas-agent"
    tracker = MentionTracker(tmp_path / "processed_mentions.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    zendesk_call = AsyncMock(
        return_value={
            "success": True,
            "data": {"comments": [comment], "next_page": None},
        }
    )

    with patch(
        "zd_agent.services.trigger_message_handler.call_zendesk_tool",
        zendesk_call,
        create=True,
    ):
        result = await handle_trigger_message(
            msg={
                "ticket_id": 12345,
                "source": "mention_candidate",
                "comment_id": str(comment["id"]),
                "_queue_key": f"mention:{comment['id']}",
            },
            manager=manager,
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            mention_tracker=tracker,
        )

    assert result.action == "mention_candidate_invalid"
    assert tracker.is_processed(comment["id"]) is True
    manager.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_self_authored_api_mention_candidate_is_safely_skipped(tmp_path) -> None:
    cfg = _cfg()
    cfg.zendesk.mention_trigger = "@saas-agent"
    tracker = MentionTracker(tmp_path / "processed_mentions.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    comment = {
        "id": "self-1",
        "public": False,
        "body": "@saas-agent generated follow-up",
        "author_id": cfg.zendesk.agent_user_id,
        "via": {"channel": "api"},
    }
    zendesk_call = AsyncMock(
        return_value={
            "success": True,
            "data": {"comments": [comment], "next_page": None},
        }
    )

    with (
        patch(
            "zd_agent.services.trigger_message_handler.call_zendesk_tool",
            zendesk_call,
            create=True,
        ),
        patch(
            "zd_agent.mention.detector.get_self_user_id",
            return_value=cfg.zendesk.agent_user_id,
        ),
    ):
        result = await handle_trigger_message(
            msg={
                "ticket_id": 12345,
                "source": "mention_candidate",
                "comment_id": "self-1",
                "_queue_key": "mention:self-1",
            },
            manager=manager,
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            mention_tracker=tracker,
        )

    assert result.action == "mention_candidate_invalid"
    assert tracker.is_processed("self-1") is True
    manager.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmaterialized_mention_candidate_defers_without_marking(
    tmp_path,
) -> None:
    cfg = _cfg()
    cfg.zendesk.mention_trigger = "@saas-agent"
    tracker = MentionTracker(tmp_path / "processed_mentions.json")
    zendesk_call = AsyncMock(
        return_value={
            "success": True,
            "data": {"comments": [], "next_page": None},
        }
    )

    with (
        patch(
            "zd_agent.services.trigger_message_handler.call_zendesk_tool",
            zendesk_call,
            create=True,
        ),
        pytest.raises(TriggerDeferredError) as exc_info,
    ):
        await handle_trigger_message(
            msg={
                "ticket_id": 12345,
                "source": "mention_candidate",
                "comment_id": "not-visible-yet",
                "_queue_key": "mention:not-visible-yet",
            },
            manager=MagicMock(),
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            mention_tracker=tracker,
        )

    assert exc_info.value.reason == "ticket_not_materialized"
    assert tracker.is_processed("not-visible-yet") is False


@pytest.mark.asyncio
async def test_no_customer_messages_logs_skip(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    log_path = tmp_path / "trigger.jsonl"

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=manager,
        state=state,
        cfg=_cfg(),
        log_path=log_path,
        fetch_customer_messages_fn=AsyncMock(return_value=([], {"id": 12345})),
    )

    assert result.handled is True
    assert result.action == "no_customer_message"
    manager.dispatch.assert_not_awaited()
    [record] = log_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(record)
    assert payload["ticket"] == "12345"
    assert payload["verdict"] == "no_customer_message"
    assert payload["action"] == "skip"


@pytest.mark.asyncio
async def test_ticket_fetch_failure_is_retryable(tmp_path) -> None:
    with pytest.raises(TriggerDeferredError, match="ticket_not_materialized"):
        await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "zendesk_webhook"},
            manager=MagicMock(),
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=_cfg(),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(return_value=([], None)),
        )


@pytest.mark.asyncio
async def test_customer_message_during_cooldown_is_deferred_without_consuming_it(
    tmp_path,
) -> None:
    now = datetime.now(timezone.utc)
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="processed",
            first_seen=now - timedelta(minutes=10),
            last_agent_finish_ts=now - timedelta(seconds=20),
            last_customer_comment_id="cid-1",
        ),
    )

    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "zendesk_webhook"},
            manager=MagicMock(),
            state=state,
            cfg=_cfg(),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(
                return_value=(
                    [
                        {
                            "id": "cid-2",
                            "body": "new issue",
                            "created_at": now.isoformat(),
                        }
                    ],
                    {"id": 12345},
                )
            ),
            human_takeover_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert exc_info.value.reason == "post_agent_cooldown"
    assert 0 < exc_info.value.delay_seconds <= 180
    assert state.get(12345).last_customer_comment_id == "cid-1"


@pytest.mark.asyncio
async def test_customer_message_enters_v2_without_legacy_classification(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=manager,
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(
            return_value=(
                [
                    {
                        "id": "cid-1",
                        "body": "it fails",
                        "created_at": "2026-06-05T06:00:00Z",
                    }
                ],
                {"id": 12345},
            )
        ),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.handled is True
    assert result.action == "casework_queued"
    queued = state.get(12345)
    assert queued is not None
    assert queued.last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_new_customer_message_reopens_retry_exhausted_ownership(
    tmp_path,
) -> None:
    now = datetime.now(timezone.utc)
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="owned",
            first_seen=now - timedelta(days=1),
            last_customer_comment_id="cid-old",
            last_agent_finish_ts=now - timedelta(seconds=10),
            consecutive_agent_failures=3,
            ownership_action="support",
            ownership_reason="automation_retry_exhausted",
            ownership_effect_reference="casework-effect:old-failure",
        ),
    )
    notify = AsyncMock(return_value=True)

    with patch(
        "zd_agent.services.owned_customer_followup.notify_owned_customer_followup",
        notify,
    ):
        result = await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "messaging_webhook"},
            manager=MagicMock(),
            state=state,
            cfg=_cfg(),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(
                return_value=(
                    [
                        {
                            "id": "cid-old",
                            "body": "original question",
                            "created_at": (now - timedelta(minutes=10)).isoformat(),
                        },
                        {
                            "id": "cid-new",
                            "body": "new question after recovery",
                            "created_at": now.isoformat(),
                        },
                    ],
                    {"id": 12345, "status": "open"},
                ),
            ),
            human_takeover_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert result.action == "casework_queued"
    reopened = state.get(12345)
    assert reopened is not None
    assert reopened.state == "in_silence_window"
    assert reopened.last_customer_comment_id == "cid-new"
    assert reopened.last_agent_finish_ts is None
    assert reopened.consecutive_agent_failures == 0
    assert reopened.ownership_action == ""
    assert reopened.ownership_reason == ""
    assert reopened.ownership_effect_reference == ""
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_notified_retry_exhausted_message_reopens_when_poller_replays(
    tmp_path,
) -> None:
    now = datetime.now(timezone.utc)
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="owned",
            first_seen=now - timedelta(days=1),
            last_customer_comment_id="cid-current",
            consecutive_agent_failures=3,
            last_verdict="owned_customer_followup_notified",
            ownership_action="support",
            ownership_reason="automation_retry_exhausted",
            ownership_effect_reference="casework-effect:old-failure",
        ),
    )
    notify = AsyncMock(return_value=True)

    with patch(
        "zd_agent.services.owned_customer_followup.notify_owned_customer_followup",
        notify,
    ):
        result = await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "poller_new_msg"},
            manager=MagicMock(),
            state=state,
            cfg=_cfg(),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(
                return_value=(
                    [
                        {
                            "id": "cid-current",
                            "body": "message consumed by the old handoff path",
                            "created_at": now.isoformat(),
                        }
                    ],
                    {"id": 12345, "status": "open"},
                ),
            ),
            human_takeover_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert result.action == "casework_queued"
    reopened = state.get(12345)
    assert reopened is not None
    assert reopened.state == "in_silence_window"
    assert reopened.last_customer_comment_id == "cid-current"
    assert reopened.consecutive_agent_failures == 0
    assert reopened.ownership_reason == ""
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_customer_message_keeps_confirmed_human_ownership(
    tmp_path,
) -> None:
    now = datetime.now(timezone.utc)
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="owned",
            first_seen=now - timedelta(days=1),
            last_customer_comment_id="cid-old",
            ownership_action="engineering",
            ownership_reason="confirmed_platform_defect",
            ownership_effect_reference="casework-effect:engineering",
        ),
    )
    notify = AsyncMock(return_value=True)

    with patch(
        "zd_agent.services.owned_customer_followup.notify_owned_customer_followup",
        notify,
    ):
        result = await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "messaging_webhook"},
            manager=MagicMock(),
            state=state,
            cfg=_cfg(),
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=AsyncMock(
                return_value=(
                    [
                        {
                            "id": "cid-new",
                            "body": "new evidence for engineering",
                            "created_at": now.isoformat(),
                        }
                    ],
                    {"id": 12345, "status": "open"},
                ),
            ),
            human_takeover_fn=AsyncMock(),
            detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        )

    assert result.action == "owned_customer_followup"
    owned = state.get(12345)
    assert owned is not None
    assert owned.state == "owned"
    assert owned.ownership_action == "engineering"
    assert owned.ownership_reason == "confirmed_platform_defect"
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_casework_mode_queues_v2_without_legacy_verdict_or_public_template(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    cfg = _cfg()
    cfg.casework.mode = ExecutionMode.CASEWORK

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=MagicMock(),
        state=state,
        cfg=cfg,
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(
            return_value=(
                [
                    {
                        "id": "cid-1",
                        "body": "hello",
                        "created_at": "2026-06-05T06:00:00Z",
                    }
                ],
                {"id": 12345},
            )
        ),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "casework_queued"
    queued = state.get(12345)
    assert queued is not None
    assert queued.state == "in_silence_window"
    assert queued.last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_canary_selected_ticket_queues_v2_without_legacy_fallback(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    cfg = _cfg()
    cfg.casework.mode = ExecutionMode.CANARY
    cfg.casework.canary_ticket_ids = (12345,)

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=MagicMock(),
        state=state,
        cfg=cfg,
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(
            return_value=(
                [
                    {
                        "id": "cid-1",
                        "body": "hello",
                        "created_at": "2026-06-05T06:00:00Z",
                    }
                ],
                {"id": 12345},
            )
        ),
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "casework_queued"
    assert state.get(12345).last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_canary_non_selected_ticket_is_deferred_before_external_actions(
    tmp_path,
) -> None:
    cfg = _cfg()
    cfg.casework.mode = ExecutionMode.CANARY
    cfg.casework.canary_ticket_ids = (99999,)
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    fetch = AsyncMock()
    human_takeover = AsyncMock()

    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg={"ticket_id": 12345, "source": "zendesk_webhook"},
            manager=manager,
            state=TicketStateTracker(tmp_path / "tickets.json"),
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=fetch,
            human_takeover_fn=human_takeover,
        )

    assert exc_info.value.reason == "casework_canary_not_selected"
    assert exc_info.value.delay_seconds == 60.0
    fetch.assert_not_awaited()
    manager.dispatch.assert_not_awaited()
    human_takeover.assert_not_awaited()


@pytest.mark.parametrize("source", ["mention", "silence_waker"])
@pytest.mark.asyncio
async def test_canary_non_selected_direct_source_never_dispatches(
    tmp_path,
    source: str,
) -> None:
    cfg = _cfg()
    cfg.casework.mode = ExecutionMode.CANARY
    cfg.casework.canary_ticket_ids = ()
    state = TicketStateTracker(tmp_path / f"tickets-{source}.json")
    msg = {"ticket_id": 12345, "source": source}
    if source == "silence_waker":
        now = datetime.now(timezone.utc)
        state.set(
            12345,
            TicketState(
                state="in_silence_window",
                first_seen=now,
                silence_window_end=now + timedelta(minutes=1),
            ),
        )
        current = state.get(12345)
        msg.update(
            expected_state_revision=current.state_revision,
            expected_silence_window_end=current.silence_window_end.isoformat(),
        )
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    fetch = AsyncMock()

    with pytest.raises(TriggerDeferredError) as exc_info:
        await handle_trigger_message(
            msg=msg,
            manager=manager,
            state=state,
            cfg=cfg,
            log_path=tmp_path / "trigger.jsonl",
            fetch_customer_messages_fn=fetch,
            human_takeover_fn=AsyncMock(),
        )

    assert exc_info.value.reason == "casework_canary_not_selected"
    manager.dispatch.assert_not_awaited()
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_recovery_reenters_takeover_and_v2_intake_gates(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    fetch_messages = AsyncMock(
        return_value=(
            [
                {
                    "id": "cid-1",
                    "body": "issue detail",
                    "created_at": "2026-06-05T06:00:00Z",
                }
            ],
            {"id": 12345},
        )
    )
    detect_takeover = AsyncMock(return_value=(False, ""))

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "startup_recovery"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch_messages,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=detect_takeover,
    )

    assert result.action == "casework_queued"
    fetch_messages.assert_awaited_once()
    detect_takeover.assert_awaited_once()
    assert state.get(12345).last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_startup_recovery_without_messages_does_not_overwrite_newer_state(
    tmp_path,
) -> None:
    """A stale startup worker must not resurrect a ticket after human takeover."""
    state = TicketStateTracker(tmp_path / "tickets.json")
    now = datetime.now(timezone.utc)
    state.set(
        12345,
        TicketState(
            state="processing",
            first_seen=now,
            active_session_id="crashed-session",
        ),
    )
    before_fetch = state.get(12345)
    assert before_fetch is not None

    async def fetch(_ticket_id: int):
        current = state.get(12345)
        assert current is not None
        current.state = "processing"
        current.active_session_id = "new-live-session"
        state.set(12345, current)
        return [], {"id": 12345}

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "startup_recovery"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch,
        human_takeover_fn=AsyncMock(),
    )

    assert result.action == "startup_recovery_waiting"
    assert state.get(12345).state == "processing"
    assert state.get(12345).active_session_id == "new-live-session"


@pytest.mark.asyncio
async def test_replayed_webhook_does_not_create_a_second_casework_generation(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")

    async def fetch(_ticket_id: int):
        return (
            [{"id": "m1", "body": "it fails", "created_at": "2026-06-05T06:00:00Z"}],
            {"id": 12345},
        )

    kwargs = dict(
        msg={"ticket_id": 12345, "source": "zendesk_webhook", "_queue_key": "event-a"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )
    first = await handle_trigger_message(**kwargs)
    first_generation = state.get(12345).state_revision
    replay = await handle_trigger_message(
        **{**kwargs, "msg": {**kwargs["msg"], "_queue_key": "event-b"}},
    )

    assert first.action == "casework_queued"
    assert replay.action == "duplicate_customer_comment"
    assert state.get(12345).state_revision == first_generation


@pytest.mark.asyncio
async def test_v2_intake_does_not_require_legacy_classifier(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")

    async def fetch(_ticket_id: int):
        return (
            [{"id": "m1", "body": "it fails", "created_at": "2026-06-05T06:00:00Z"}],
            {"id": 12345},
        )

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook", "_queue_key": "event-a"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=fetch,
        human_takeover_fn=AsyncMock(),
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
    )

    assert result.action == "casework_queued"
    assert state.get(12345).last_verdict == "casework_ready"


@pytest.mark.asyncio
async def test_pending_ticket_is_outside_automatic_reply_scope(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    takeover = AsyncMock()

    result = await handle_trigger_message(
        msg={"ticket_id": 12345, "source": "zendesk_webhook"},
        manager=MagicMock(),
        state=state,
        cfg=_cfg(),
        log_path=tmp_path / "trigger.jsonl",
        fetch_customer_messages_fn=AsyncMock(
            return_value=(
                [{"id": "m1", "body": "new detail"}],
                {"id": 12345, "status": "pending"},
            )
        ),
        human_takeover_fn=takeover,
    )

    assert result.action == "status_out_of_scope"
    takeover.assert_not_awaited()
