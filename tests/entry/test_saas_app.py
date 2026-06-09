"""Test SaaS app factory creates app with expected routes."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from zd.client import ZendeskError

from zd_agent.runtime.retry import RetryPolicy


def test_create_saas_app_has_expected_routes(tmp_path):
    from zd_agent.services.saas_app import create_saas_app

    with patch("zd_agent.services.saas_app.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.resources.base_dir = str(tmp_path)
        cfg.resources.state_dir = ".state"
        cfg.webhook.zendesk_path = "/webhook/zendesk"
        cfg.webhook.zendesk_messaging_path = "/webhook/zendesk-messaging"
        mock_cfg.return_value = cfg

        manager = MagicMock()
        app = create_saas_app(manager)

    route_paths = {r.resource.canonical for r in app.router.routes()}
    assert "/webhook/zendesk" in route_paths
    assert "/webhook/zendesk-messaging" in route_paths
    assert "/internal/pre-send-freshness" in route_paths
    assert "/healthz" in route_paths
    assert "/webhook/feishu" not in route_paths


def test_create_saas_app_wires_queues_and_state(tmp_path):
    from zd_agent.services.saas_app import create_saas_app
    from zd_agent.services.app_context import session_manager, ticket_state, trigger_queue

    with patch("zd_agent.services.saas_app.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.resources.base_dir = str(tmp_path)
        cfg.resources.state_dir = ".state"
        cfg.webhook.zendesk_path = "/webhook/zendesk"
        cfg.webhook.zendesk_messaging_path = "/webhook/zendesk-messaging"
        mock_cfg.return_value = cfg

        manager = MagicMock()
        app = create_saas_app(manager)

    assert trigger_queue(app) is not None
    assert ticket_state(app) is not None
    assert session_manager(app) is manager


@pytest.mark.asyncio
async def test_completed_reply_session_marks_waiting_ticket_processed(tmp_path):
    from zd_agent.services.saas_app import create_saas_app
    from zd_agent.services.app_context import ticket_state
    from zd_agent.session.agent_session import AgentSession, SessionStatus
    from zd_agent.trigger.state_store import TicketState

    with patch("zd_agent.services.saas_app.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.resources.base_dir = str(tmp_path)
        cfg.resources.state_dir = ".state"
        cfg.webhook.zendesk_path = "/webhook/zendesk"
        cfg.webhook.zendesk_messaging_path = "/webhook/zendesk-messaging"
        mock_cfg.return_value = cfg

        manager = MagicMock()
        app = create_saas_app(manager)

    state = ticket_state(app)
    state.set("2808", TicketState(
        state="waiting_for_issue",
        first_seen=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ),
        last_verdict="insufficient_info",
    ))
    callback = manager.set_on_finished.call_args.args[0]
    session = AgentSession(ticket_id=2808, trigger_type="webhook-mention")
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True

    await callback(session)

    rec = state.get("2808")
    assert rec is not None
    assert rec.state == "processed"
    assert rec.last_agent_finish_ts is not None


def test_messaging_image_event_yields_image_url():
    """Conversation log image content 应被识别为客户消息且 image_urls 含 media_url。"""
    from zd_agent.services.ticket_messages import _messaging_customer_messages

    events = [
        {
            "id": "evt-1",
            "created_at": "2026-04-26T13:50:00Z",
            "author": {"type": "end-user", "zen:support:user_id": 999},
            "content": {
                "type": "image",
                "alt_text": "Screen Shot 2025-01-01.png",
                "media_url": "https://media.smooch.io/abc/xyz.png",
            },
        },
    ]
    msgs = _messaging_customer_messages(events)
    assert len(msgs) == 1
    m = msgs[0]
    assert m["image_urls"] == ["https://media.smooch.io/abc/xyz.png"]
    assert "客户附图" in m["body"]


def test_customer_public_comments_extracts_image_attachments():
    """comments.json 里 author=requester 的 public 评论里的图片附件应被抽到 image_urls。"""
    from zd_agent.services.ticket_messages import _customer_public_comments

    ticket = {"requester_id": 42}
    comments = [
        {
            "id": 1,
            "author_id": 42,
            "public": True,
            "body": "see screenshot",
            "created_at": "2026-04-26T10:00:00Z",
            "attachments": [
                {
                    "content_type": "image/png",
                    "content_url": "https://example.zendesk.com/abc.png",
                    "file_name": "abc.png",
                },
                {
                    "content_type": "text/plain",
                    "content_url": "https://example.zendesk.com/log.txt",
                },
            ],
        },
        {
            "id": 2,
            "author_id": 99,  # not requester
            "public": True,
            "attachments": [
                {"content_type": "image/png", "content_url": "https://nope/x.png"},
            ],
        },
    ]
    out = _customer_public_comments(ticket, comments)
    assert len(out) == 1
    assert out[0]["image_urls"] == ["https://example.zendesk.com/abc.png"]


@pytest.mark.asyncio
async def test_collect_recent_image_data_urls_downloads_and_filters_failures():
    """_collect_recent_image_data_urls 取最近 lookback 条的图，逐张下载，失败的丢掉。"""
    from zd_agent.services import ticket_messages

    customer_msgs = [
        {"image_urls": ["http://old/should_skip.png"]},  # outside lookback
        {"image_urls": ["http://a/1.png"]},
        {"image_urls": ["http://a/2.png", "http://a/3.png"]},
    ]

    async def fake_dl(url, **kwargs):
        if "2.png" in url:
            return None  # 模拟下载失败
        return f"data:image/png;base64,FAKE-{url[-5:]}"

    with patch.object(ticket_messages, "_download_image_data_url", side_effect=fake_dl):
        out = await ticket_messages._collect_recent_image_data_urls(
            customer_msgs, lookback=2, budget=5,
        )
    # old/skip.png 在 lookback 之外不应取
    assert all("should_skip" not in u for u in out)
    # 2.png 失败被过滤
    assert len(out) == 2
    assert all(u.startswith("data:image/png;base64,FAKE-") for u in out)


@pytest.mark.asyncio
async def test_fetch_customer_messages_retries_transient_zendesk_network_error():
    from zd_agent.services.ticket_messages import _fetch_customer_messages

    client = MagicMock()
    client.get_ticket = MagicMock(side_effect=[
        ZendeskError(0, "网络连接失败，请检查 ZENDESK_SUBDOMAIN 是否正确"),
        {"ticket": {
            "id": 2808,
            "requester_id": 42,
            "via": {"channel": "web"},
        }},
    ])
    client.get_ticket_comments = MagicMock(return_value={
        "comments": [
            {
                "id": "c1",
                "author_id": 42,
                "public": True,
                "body": "I cannot change my email",
                "created_at": "2026-06-04T15:52:00Z",
            },
        ],
    })

    with patch("zd.client.ZendeskClient", return_value=client), \
         patch(
             "zd_agent.tools.zendesk.get_retry_policy",
             return_value=RetryPolicy(
                 name="zendesk",
                 max_attempts=2,
                 base_delay_seconds=0,
                 max_delay_seconds=0,
                 jitter=False,
             ),
         ):
        msgs, ticket = await _fetch_customer_messages(2808)

    assert ticket["id"] == 2808
    assert [m["id"] for m in msgs] == ["c1"]
    assert client.get_ticket.call_count == 2
