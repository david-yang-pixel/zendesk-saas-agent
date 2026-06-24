"""Test Feishu custom-bot client + card builders."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zd_agent.integrations.feishu.cards import (
    alert_card,
    auth_renew_card,
    daily_summary_card,
    ticket_done_card,
    ticket_escalate_card,
)
from zd_agent.integrations.feishu.client import FeishuClient, _gen_sign


# ===== Card builder tests (unchanged) =====

def _div_text_contents(card: dict) -> list[str]:
    return [
        element["text"]["content"]
        for element in card["elements"]
        if element.get("tag") == "div" and "text" in element
    ]


def test_ticket_done_card_has_ticket_id_in_title():
    card = ticket_done_card(
        ticket_id="12345", summary="ok", zendesk_url="https://z.example.com/12345",
    )
    assert card["header"]["template"] == "green"
    assert "12345" in card["header"]["title"]["content"]


def test_ticket_done_card_without_url_has_no_view_button():
    card = ticket_done_card(ticket_id="12345", summary="ok", zendesk_url="")
    actions = card["elements"][-1]["actions"]
    button_texts = [a["text"]["content"] for a in actions]
    assert "查看 Zendesk" not in button_texts
    assert "重新处理" in button_texts


def test_ticket_done_card_renders_labeled_summary_as_readable_sections():
    card = ticket_done_card(
        ticket_id="3066",
        summary=(
            "处理结果：已回复客户\n"
            "关键事实：workspace subscription is healthy\n"
            "客户回复：asked customer to retry /subscription/info\n"
            "后续动作：no manual follow-up needed\n"
            "注意事项：SigNoz unavailable, traceback not retrieved"
        ),
        zendesk_url="https://z.example.com/3066",
    )

    assert _div_text_contents(card)[:5] == [
        "**处理结果**\n已回复客户",
        "**关键事实**\nworkspace subscription is healthy",
        "**客户回复**\nasked customer to retry /subscription/info",
        "**后续动作**\nno manual follow-up needed",
        "**注意事项**\nSigNoz unavailable, traceback not retrieved",
    ]
    assert card["elements"][-1]["tag"] == "action"


def test_ticket_done_card_splits_plain_summary_into_bullet_digest():
    summary = (
        "Ticket #3066 handled (billing). "
        "Workspace 5f1000d5 subscription is healthy in billing DB: plan=team, active. "
        "Customer error maps to `api/services/billing_service.py:337`. "
        "Public reply sent asking customer to retry and provide endpoint/timestamp."
    )

    card = ticket_done_card(ticket_id="3066", summary=summary, zendesk_url="")
    contents = _div_text_contents(card)

    assert contents == [
        "**摘要**\n"
        "- Ticket #3066 handled (billing).\n"
        "- Workspace 5f1000d5 subscription is healthy in billing DB: plan=team, active.\n"
        "- Customer error maps to `api/services/billing_service.py:337`.\n"
        "- Public reply sent asking customer to retry and provide endpoint/timestamp."
    ]
    assert max(len(line) for line in contents[0].splitlines()) < len(summary)


def test_ticket_done_card_caps_long_plain_summary_sentence():
    summary = " ".join(["Long customer-facing update"] * 80) + " tail-marker."

    card = ticket_done_card(ticket_id="3066", summary=summary, zendesk_url="")
    content = _div_text_contents(card)[0]

    assert "tail-marker" not in content
    assert len(content) < 500


def test_ticket_escalate_template_is_orange():
    card = ticket_escalate_card(
        ticket_id="1", reason="missing info", zendesk_url="https://z/x",
    )
    assert card["header"]["template"] == "orange"
    assert "missing info" in str(card)


def test_auth_renew_card_contains_url():
    card = auth_renew_card(
        url="https://teleport.dify.dev/headless/abc", user="me",
    )
    assert card["header"]["template"] == "red"
    assert "headless" in str(card["elements"])


def test_daily_summary_card_shows_stats():
    card = daily_summary_card(
        date="2026-04-22",
        stats={"total": 15, "success": 12, "failed": 1, "escalated": 2, "refunds": 2},
    )
    content = card["elements"][0]["text"]["content"]
    assert "15" in content
    assert "12" in content


def test_alert_card_urgent_uses_red_template():
    card = alert_card(title="T", message="M", urgent=True)
    assert card["header"]["template"] == "red"
    assert "🚨" in card["header"]["title"]["content"]


def test_alert_card_non_urgent_uses_yellow():
    card = alert_card(title="T", message="M", urgent=False)
    assert card["header"]["template"] == "yellow"
    assert "⚠️" in card["header"]["title"]["content"]


# ===== Custom-bot webhook client tests =====


def _mock_response(status: int, json_body: dict):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


class _AsyncClientCtx:
    def __init__(self, mock):
        self.mock = mock
    async def __aenter__(self):
        return self.mock
    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_send_card_posts_to_webhook_url():
    client = FeishuClient(webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test")
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=_mock_response(200, {"code": 0, "msg": "success"}))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        await client.send_alert(title="T", message="M")
    mock_http.post.assert_awaited_once()
    call = mock_http.post.await_args
    assert call.args[0] == "https://open.feishu.cn/open-apis/bot/v2/hook/test"
    body = call.kwargs["json"]
    assert body["msg_type"] == "interactive"
    assert "card" in body
    # No secret → no timestamp/sign
    assert "timestamp" not in body
    assert "sign" not in body


@pytest.mark.asyncio
async def test_send_card_with_secret_includes_sign():
    client = FeishuClient(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        webhook_secret="mysecret",
    )
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=_mock_response(200, {"code": 0, "msg": "success"}))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        await client.send_alert(title="T", message="M")
    body = mock_http.post.await_args.kwargs["json"]
    assert "timestamp" in body
    assert "sign" in body
    # Verify sign is valid for the timestamp
    assert body["sign"] == _gen_sign(int(body["timestamp"]), "mysecret")


@pytest.mark.asyncio
async def test_send_card_raises_on_non_zero_code():
    client = FeishuClient(webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test")
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=_mock_response(200, {"code": 9499, "msg": "invalid token"}))
    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        with pytest.raises(RuntimeError, match="9499"):
            await client.send_alert(title="T", message="M")


@pytest.mark.asyncio
async def test_send_card_retries_feishu_rate_limit_code_11232():
    client = FeishuClient(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        retry_delays=(0, 0),
    )
    mock_http = MagicMock()
    mock_http.post = AsyncMock(side_effect=[
        _mock_response(200, {"code": 11232, "msg": "frequency limited"}),
        _mock_response(200, {"code": 0, "msg": "success"}),
    ])

    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        await client.send_alert(title="T", message="M")

    assert mock_http.post.await_count == 2


@pytest.mark.asyncio
async def test_send_card_retries_transport_error_once():
    client = FeishuClient(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        retry_delays=(0,),
    )
    mock_http = MagicMock()
    request = httpx.Request("POST", client.webhook_url)
    mock_http.post = AsyncMock(side_effect=[
        httpx.ConnectError("network down", request=request),
        _mock_response(200, {"code": 0, "msg": "success"}),
    ])

    with patch("httpx.AsyncClient", return_value=_AsyncClientCtx(mock_http)):
        await client.send_alert(title="T", message="M")

    assert mock_http.post.await_count == 2


@pytest.mark.asyncio
async def test_send_card_raises_on_missing_webhook_url():
    client = FeishuClient(webhook_url="")
    with pytest.raises(RuntimeError, match="webhook_url"):
        await client.send_alert(title="T", message="M")


def test_gen_sign_reproducible():
    """Sign is deterministic given (timestamp, secret)."""
    s1 = _gen_sign(1700000000, "secret")
    s2 = _gen_sign(1700000000, "secret")
    assert s1 == s2
    assert _gen_sign(1700000001, "secret") != s1  # different ts → different sign
