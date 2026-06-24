"""Test mention_poller: detect @mention internal notes + push to trigger_queue."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_scan_once_dispatches_mention(queue, tracker):
    """评论里有 @saas-agent 的 internal note → 推 trigger_queue。"""
    fake_search = MagicMock(return_value={"results": [_ticket(2316)]})
    fake_comments = MagicMock(return_value={
        "comments": [
            _comment("c1", "earlier note (no mention)"),
            _comment("c2", "@saas-agent 自动续订有没有可能是自动续费完用户自动取消的"),
        ],
    })
    fake_client = MagicMock(
        search_tickets=fake_search,
        get_ticket_comments=fake_comments,
    )

    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        n, new_cursor = await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=time.time() - 600,
        )

    assert n == 1
    msg = queue.get_nowait()
    assert msg["source"] == "mention"
    assert msg["ticket_id"] == 2316
    assert msg["comment_id"] == "c2"
    assert "saas-agent" in msg["hint"]
    # tracker should mark this comment processed
    assert tracker.is_processed("c2")


@pytest.mark.asyncio
async def test_scan_once_dispatches_mention_alias(queue, tracker):
    """@yonggang alias should dispatch while preserving @saas-agent support."""
    fake_search = MagicMock(return_value={"results": [_ticket(2886, status="new")]})
    fake_comments = MagicMock(return_value={
        "comments": [_comment("c-alias", "**yonggang** 处理一下")],
    })
    fake_client = MagicMock(
        search_tickets=fake_search,
        get_ticket_comments=fake_comments,
    )

    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = ["@yonggang"]
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=time.time() - 600,
        )

    assert n == 1
    msg = queue.get_nowait()
    assert msg["ticket_id"] == 2886
    assert msg["comment_id"] == "c-alias"


@pytest.mark.asyncio
async def test_scan_once_finds_mentions_after_first_comment_page(queue, tracker):
    """Regression: #2861 had 11 comments; page 1/per_page 10 missed newest mention."""
    fake_search = MagicMock(return_value={"results": [_ticket(2861, status="open")]})

    def fake_comments(ticket_id, page=1, per_page=100):
        assert ticket_id == 2861
        if page == 1:
            return {
                "comments": [
                    _comment(f"c{i}", f"old comment {i}")
                    for i in range(1, 11)
                ],
                "next_page": "page-2",
            }
        if page == 2:
            return {
                "comments": [_comment("c11", "**yonggang**")],
                "next_page": None,
            }
        raise AssertionError(f"unexpected page {page}")

    fake_client = MagicMock(
        search_tickets=fake_search,
        get_ticket_comments=MagicMock(side_effect=fake_comments),
    )

    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg.zendesk.mention_aliases = ["@yonggang"]
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=time.time() - 600,
        )

    assert n == 1
    msg = queue.get_nowait()
    assert msg["ticket_id"] == 2861
    assert msg["comment_id"] == "c11"
    assert tracker.is_processed("c11")


@pytest.mark.asyncio
async def test_scan_once_skips_processed(queue, tracker):
    """已经 mark_processed 的 mention 不再 dispatch（幂等）。"""
    tracker.mark_processed("c-already")

    fake_search = MagicMock(return_value={"results": [_ticket(2316)]})
    fake_comments = MagicMock(return_value={
        "comments": [_comment("c-already", "@saas-agent please look")],
    })
    fake_client = MagicMock(
        search_tickets=fake_search, get_ticket_comments=fake_comments,
    )

    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=time.time() - 600,
        )

    assert n == 0
    assert queue.empty()


@pytest.mark.asyncio
async def test_scan_once_skips_public_comments(queue, tracker):
    """is_public=True 的评论即使提到 @saas-agent 也不算 mention（按 detector 规则）。"""
    fake_search = MagicMock(return_value={"results": [_ticket(2316)]})
    fake_comments = MagicMock(return_value={
        "comments": [_comment("c-pub", "@saas-agent here is the answer", public=True)],
    })
    fake_client = MagicMock(
        search_tickets=fake_search, get_ticket_comments=fake_comments,
    )

    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=time.time() - 600,
        )
    assert n == 0


@pytest.mark.asyncio
async def test_scan_once_advances_cursor_on_success(queue, tracker):
    """成功扫描后 cursor 应 advance 到 ~now（避免下轮重复扫同时段）。"""
    fake_search = MagicMock(return_value={"results": []})
    fake_client = MagicMock(search_tickets=fake_search)

    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        old = time.time() - 600
        n, new = await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=old,
        )
    assert n == 0
    assert new > old + 500  # advanced significantly


@pytest.mark.asyncio
async def test_scan_once_searches_with_overlap_window(queue, tracker):
    """Zendesk search index can lag, so mention polling must overlap windows."""
    fake_search = MagicMock(return_value={"results": []})
    fake_client = MagicMock(search_tickets=fake_search)

    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        old = 1_780_591_900
        await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=old,
        )

    query = fake_search.call_args.args[0]
    expected_since = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(old - _SEARCH_LOOKBACK_SECONDS),
    )
    assert f"updated>{expected_since}" in query
    assert query.startswith("status<solved ")


@pytest.mark.asyncio
async def test_scan_once_keeps_cursor_on_search_failure(queue, tracker):
    """search API 失败 → 不 advance cursor，下轮重试同时段。"""
    fake_search = MagicMock(side_effect=RuntimeError("api 500"))
    fake_client = MagicMock(search_tickets=fake_search)

    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        old = time.time() - 600
        n, new = await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=old,
        )
    assert n == 0
    assert new == old  # cursor unchanged


@pytest.mark.asyncio
async def test_scan_once_handles_per_ticket_comment_failure(queue, tracker):
    """get_ticket_comments fail for one ticket → 跳过它，继续扫别的 ticket。"""
    fake_search = MagicMock(return_value={
        "results": [_ticket(2316), _ticket(2317)],
    })
    # First ticket comments fail, second succeeds with mention
    fake_comments = MagicMock(side_effect=[
        RuntimeError("rate limit"),
        {"comments": [_comment("c-good", "@saas-agent please")]},
    ])
    fake_client = MagicMock(
        search_tickets=fake_search, get_ticket_comments=fake_comments,
    )
    with patch("zd_agent.services.mention_poller.get_config") as cfg_mock, \
         patch("zd.client.ZendeskClient", return_value=fake_client):
        cfg = MagicMock()
        cfg.zendesk.mention_trigger = "@saas-agent"
        cfg_mock.return_value = cfg

        n, _ = await scan_once(
            trigger_queue=queue, tracker=tracker, last_poll_unix=time.time() - 600,
        )
    assert n == 1  # only ticket 2317 dispatched
    msg = queue.get_nowait()
    assert msg["ticket_id"] == 2317
