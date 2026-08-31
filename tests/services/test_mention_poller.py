"""Test mention_poller: detect @mention internal notes + push to trigger_queue."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from zd_agent.services.mention_poller import _SEARCH_LOOKBACK_SECONDS, scan_once
from zd_agent.mention.tracker import MentionTracker


@pytest.fixture
def queue():
    return asyncio.Queue()


@pytest.fixture
def tracker(tmp_path):
    return MentionTracker(state_file=tmp_path / "processed_mentions.json")


def _ticket(tid=2316, status="open"):
    return {"id": tid, "status": status}


def _comment(cid, body, *, public=False, author_id=999):
    return {
        "id": cid,
        "body": body,
        "is_public": public,
        "author_id": author_id,
    }


def _mcp_call(
    *,
    search_resp: dict | None = None,
    comments_by_ticket: dict[int, object] | None = None,
    search_error: str | None = None,
):
    state = {"search_args": [], "comment_args": []}
    comments_by_ticket = comments_by_ticket or {}

    async def fake_call(name: str, arguments: dict):
        if name == "search_tickets":
            state["search_args"].append(arguments)
            if search_error:
                return {
                    "success": False,
                    "code": "ZENDESK_API_ERROR",
                    "error": search_error,
                }
            return {"success": True, "data": search_resp or {"results": []}}
        if name == "get_ticket_comments":
            state["comment_args"].append(arguments)
            ticket_id = arguments["ticket_id"]
            spec = comments_by_ticket.get(ticket_id, {"comments": []})
            if isinstance(spec, BaseException):
                return {
                    "success": False,
                    "code": "ZENDESK_API_ERROR",
                    "error": str(spec),
                }
            if callable(spec):
                spec = spec(arguments)
            return {"success": True, "data": spec}
        raise AssertionError(f"unexpected Zendesk MCP tool: {name}")

    return fake_call, state


@pytest.mark.asyncio
async def test_scan_once_dispatches_mention(queue, tracker):
    """评论里有 @saas-agent 的 internal note → 推 trigger_queue。"""
    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(2316)]},
        comments_by_ticket={
            2316: {
                "comments": [
                    _comment("c1", "earlier note (no mention)"),
                    _comment(
                        "c2", "@saas-agent 自动续订有没有可能是自动续费完用户自动取消的"
                    ),
                ],
            }
        },
    )

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        n, new_cursor = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )

    assert n == 1
    msg = queue.get_nowait()
    assert msg["source"] == "mention"
    assert msg["ticket_id"] == 2316
    assert msg["comment_id"] == "c2"
    assert "saas-agent" in msg["hint"]
    # The consumer marks only after a committed dispatch. Marking at enqueue
    # would race a signed webhook candidate and could cause duplicate sessions.
    assert not tracker.is_processed("c2")


@pytest.mark.asyncio
async def test_scan_once_skips_api_mention_when_agent_user_id_is_unconfigured(
    queue,
    tracker,
):
    """Regression: #3501 must not loop on Agent-authored API notes."""
    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(3501)]},
        comments_by_ticket={
            3501: {
                "comments": [
                    {
                        "id": "agent-error-note",
                        "body": (
                            "Production agent stopped. Retry by mentioning @David Yang."
                        ),
                        "public": False,
                        "author_id": 45433304089364,
                        "via": {
                            "channel": "api",
                            "source": {"from": {}, "to": {}, "rel": None},
                        },
                    }
                ],
            }
        },
    )

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
        patch(
            "zd_agent.mention.detector.get_self_user_id",
            return_value=None,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@David Yang"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg

        n, cursor = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )
        n_again, _ = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=cursor,
        )

    assert n == 0
    assert n_again == 0
    assert queue.empty()


@pytest.mark.asyncio
async def test_scan_once_does_not_mark_before_consumer_dispatch(tracker):
    class FailingQueue:
        async def put(self, _message):
            raise RuntimeError("queue unavailable")

    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(2316)]},
        comments_by_ticket={
            2316: {"comments": [_comment("c-durable", "@saas-agent please check")]},
        },
    )

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg

        with pytest.raises(RuntimeError, match="queue unavailable"):
            await scan_once(
                trigger_queue=FailingQueue(),
                tracker=tracker,
                last_poll_unix=time.time() - 600,
            )

    assert not tracker.is_processed("c-durable")


@pytest.mark.asyncio
async def test_scan_once_does_not_mark_when_webhook_candidate_owns_queue_key(tracker):
    class DuplicateQueue:
        async def put(self, _message):
            return False

    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(2316)]},
        comments_by_ticket={
            2316: {"comments": [_comment("c-webhook", "@saas-agent please check")]},
        },
    )

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg

        dispatched, _ = await scan_once(
            trigger_queue=DuplicateQueue(),
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )

    assert dispatched == 0
    assert not tracker.is_processed("c-webhook")


@pytest.mark.asyncio
async def test_scan_once_retries_same_update_after_candidate_queue_conflict(tracker):
    class CandidateRaceQueue:
        def __init__(self) -> None:
            self.calls = 0

        async def put(self, _message):
            self.calls += 1
            return self.calls > 1

    ticket = {
        **_ticket(2316),
        "updated_at": "2026-08-12T00:00:00Z",
    }
    fake_call, _state = _mcp_call(
        search_resp={"results": [ticket]},
        comments_by_ticket={
            2316: {"comments": [_comment("c-race", "@saas-agent please check")]},
        },
    )
    update_cache: dict[int, str] = {}
    queue = CandidateRaceQueue()

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg

        first, cursor = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
            ticket_update_cache=update_cache,
        )
        second, _ = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=cursor,
            ticket_update_cache=update_cache,
        )

    assert first == 0
    assert second == 1
    assert queue.calls == 2
    assert not tracker.is_processed("c-race")


@pytest.mark.asyncio
async def test_scan_once_dispatches_mention_alias(queue, tracker):
    """@yonggang alias should dispatch while preserving @saas-agent support."""
    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(2886, status="new")]},
        comments_by_ticket={
            2886: {"comments": [_comment("c-alias", "**yonggang** 处理一下")]},
        },
    )

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = ["@yonggang"]
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )

    assert n == 1
    msg = queue.get_nowait()
    assert msg["ticket_id"] == 2886
    assert msg["comment_id"] == "c-alias"


@pytest.mark.asyncio
async def test_scan_once_finds_mentions_after_first_comment_page(queue, tracker):
    """Regression: #2861 had 11 comments; page 1/per_page 10 missed newest mention."""

    def fake_comments(arguments):
        page = arguments["page"]
        if page == 1:
            return {
                "comments": [
                    _comment(f"c{i}", f"old comment {i}") for i in range(1, 11)
                ],
                "next_page": "page-2",
            }
        if page == 2:
            return {
                "comments": [_comment("c11", "**yonggang**")],
                "next_page": None,
            }
        raise AssertionError(f"unexpected page {page}")

    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(2861, status="open")]},
        comments_by_ticket={2861: fake_comments},
    )

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = ["@yonggang"]
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )

    assert n == 1
    msg = queue.get_nowait()
    assert msg["ticket_id"] == 2861
    assert msg["comment_id"] == "c11"
    assert not tracker.is_processed("c11")


@pytest.mark.asyncio
async def test_scan_once_skips_processed(queue, tracker):
    """已经 mark_processed 的 mention 不再 dispatch（幂等）。"""
    tracker.mark_processed("c-already")

    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(2316)]},
        comments_by_ticket={
            2316: {"comments": [_comment("c-already", "@saas-agent please look")]},
        },
    )

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )

    assert n == 0
    assert queue.empty()


@pytest.mark.asyncio
async def test_scan_once_skips_public_comments(queue, tracker):
    """is_public=True 的评论即使提到 @saas-agent 也不算 mention（按 detector 规则）。"""
    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(2316)]},
        comments_by_ticket={
            2316: {
                "comments": [
                    _comment("c-pub", "@saas-agent here is the answer", public=True)
                ]
            },
        },
    )

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )
    assert n == 0


@pytest.mark.asyncio
async def test_scan_once_advances_cursor_on_success(queue, tracker):
    """成功扫描后 cursor 应 advance 到 ~now（避免下轮重复扫同时段）。"""
    fake_call, _state = _mcp_call(search_resp={"results": []})

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        old = time.time() - 600
        n, new = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=old,
        )
    assert n == 0
    assert new > old + 500  # advanced significantly


@pytest.mark.asyncio
async def test_scan_once_searches_with_overlap_window(queue, tracker):
    """Zendesk search index can lag, so mention polling must overlap windows."""
    fake_call, state = _mcp_call(search_resp={"results": []})

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        old = 1_780_591_900
        await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=old,
        )

    query = state["search_args"][0]["query"]
    expected_since = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(old - _SEARCH_LOOKBACK_SECONDS),
    )
    assert f"updated>{expected_since}" in query
    assert query.startswith("status<solved ")


@pytest.mark.asyncio
async def test_scan_once_paginates_ticket_search_before_dispatch(queue, tracker):
    search_pages: list[int] = []

    async def fake_call(name: str, arguments: dict) -> dict:
        if name == "search_tickets":
            page = arguments.get("page", 1)
            search_pages.append(page)
            return {
                "success": True,
                "data": {
                    "results": [] if page == 1 else [_ticket(2317)],
                    "next_page": "page-2" if page == 1 else None,
                },
            }
        assert name == "get_ticket_comments"
        return {
            "success": True,
            "data": {"comments": [_comment("c-page-2", "@saas-agent please check")]},
        }

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg

        dispatched, _ = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )

    assert search_pages == [1, 2]
    assert dispatched == 1
    assert queue.get_nowait()["ticket_id"] == 2317


@pytest.mark.asyncio
async def test_scan_once_keeps_cursor_and_dispatches_nothing_if_later_page_fails(
    queue,
    tracker,
):
    async def fake_call(name: str, arguments: dict) -> dict:
        assert name == "search_tickets"
        if arguments.get("page", 1) == 1:
            return {
                "success": True,
                "data": {
                    "results": [_ticket(2316)],
                    "next_page": "page-2",
                },
            }
        return {
            "success": False,
            "code": "ZENDESK_API_ERROR",
            "error": "page 2 failed",
        }

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg
        old_cursor = time.time() - 600

        dispatched, new_cursor = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=old_cursor,
        )

    assert dispatched == 0
    assert new_cursor == old_cursor
    assert queue.empty()


@pytest.mark.asyncio
async def test_scan_once_bounds_broken_never_ending_search_pagination(
    queue,
    tracker,
):
    requested_pages: list[int] = []

    async def fake_call(name: str, arguments: dict) -> dict:
        assert name == "search_tickets"
        requested_pages.append(arguments["page"])
        return {
            "success": True,
            "data": {
                "results": [],
                "next_page": f"page-{arguments['page'] + 1}",
            },
        }

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
        patch("zd_agent.services.mention_poller._MAX_SEARCH_PAGES", 3),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg
        old_cursor = time.time() - 600

        dispatched, new_cursor = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=old_cursor,
        )

    assert dispatched == 0
    assert new_cursor == old_cursor
    assert requested_pages == [1, 2, 3]


@pytest.mark.asyncio
async def test_five_second_polling_does_not_refetch_unchanged_ticket_comments(
    queue,
    tracker,
):
    """The overlap window stays safe without multiplying comment API reads."""
    ticket = {
        **_ticket(3726),
        "updated_at": "2026-08-11T16:43:10Z",
    }
    fake_call, state = _mcp_call(
        search_resp={"results": [ticket]},
        comments_by_ticket={3726: {"comments": []}},
    )
    update_cache: dict[int, str] = {}

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg

        _, cursor = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 60,
            ticket_update_cache=update_cache,
        )
        await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=cursor,
            ticket_update_cache=update_cache,
        )

    assert len(state["search_args"]) == 2
    assert state["comment_args"] == [
        {"ticket_id": 3726, "page": 1, "per_page": 100},
    ]


@pytest.mark.asyncio
async def test_update_cache_forgets_tickets_outside_the_current_overlap_window(
    queue,
    tracker,
):
    ticket = {**_ticket(3726), "updated_at": "2026-08-11T16:43:10Z"}
    fake_call, _state = _mcp_call(
        search_resp={"results": [ticket]},
        comments_by_ticket={3726: {"comments": []}},
    )
    update_cache = {9999: "2026-08-01T00:00:00Z"}

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg
        await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 60,
            ticket_update_cache=update_cache,
        )

    assert update_cache == {3726: "2026-08-11T16:43:10Z"}


@pytest.mark.asyncio
async def test_update_cache_does_not_hide_a_second_unprocessed_mention(queue, tracker):
    ticket = {
        **_ticket(3726),
        "updated_at": "2026-08-11T16:43:10Z",
    }
    fake_call, _state = _mcp_call(
        search_resp={"results": [ticket]},
        comments_by_ticket={
            3726: {
                "comments": [
                    _comment("c1", "@saas-agent first request"),
                    _comment("c2", "@saas-agent second request"),
                ]
            }
        },
    )
    update_cache: dict[int, str] = {}

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool",
            side_effect=fake_call,
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = []
        cfg_mock.return_value = cfg

        _, cursor = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 60,
            ticket_update_cache=update_cache,
        )
        # The consumer owns the processed marker after its dispatch commits.
        # Once c2 completes, the next scan must drain c1 despite an unchanged
        # Zendesk updated_at value.
        tracker.mark_processed("c2")
        await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=cursor,
            ticket_update_cache=update_cache,
        )

    assert [queue.get_nowait()["comment_id"] for _ in range(2)] == ["c2", "c1"]


@pytest.mark.asyncio
async def test_scan_once_keeps_cursor_on_search_failure(queue, tracker):
    """search API 失败 → 不 advance cursor，下轮重试同时段。"""
    fake_call, _state = _mcp_call(search_error="api 500")

    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        old = time.time() - 600
        n, new = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=old,
        )
    assert n == 0
    assert new == old  # cursor unchanged


@pytest.mark.asyncio
async def test_scan_once_handles_per_ticket_comment_failure(queue, tracker):
    """get_ticket_comments fail for one ticket → 跳过它，继续扫别的 ticket。"""
    fake_call, _state = _mcp_call(
        search_resp={"results": [_ticket(2316), _ticket(2317)]},
        comments_by_ticket={
            2316: RuntimeError("rate limit"),
            2317: {"comments": [_comment("c-good", "@saas-agent please")]},
        },
    )
    with (
        patch("zd_agent.services.mention_poller.get_config") as cfg_mock,
        patch(
            "zd_agent.services.mention_poller.call_zendesk_tool", side_effect=fake_call
        ),
    ):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue,
            tracker=tracker,
            last_poll_unix=time.time() - 600,
        )
    assert n == 1  # only ticket 2317 dispatched
    msg = queue.get_nowait()
    assert msg["ticket_id"] == 2317
