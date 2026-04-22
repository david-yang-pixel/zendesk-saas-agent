"""Test feishu card builders + webhook verification. No real API calls."""
from zd_agent.integrations.feishu.cards import (
    alert_card,
    auth_renew_card,
    daily_summary_card,
    ticket_done_card,
    ticket_escalate_card,
)
from zd_agent.integrations.feishu.client import verify_webhook_request


def test_ticket_done_card_has_ticket_id_in_title():
    card = ticket_done_card(
        ticket_id="12345", summary="ok", zendesk_url="https://z.example.com/12345",
    )
    assert card["header"]["template"] == "green"
    assert "12345" in card["header"]["title"]["content"]


def test_ticket_done_card_without_url_has_no_view_button():
    card = ticket_done_card(ticket_id="12345", summary="ok", zendesk_url="")
    actions = card["elements"][-1]["actions"]
    # Only the reprocess button remains
    button_texts = [a["text"]["content"] for a in actions]
    assert "查看 Zendesk" not in button_texts
    assert "重新处理" in button_texts


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


def test_verify_webhook_request():
    assert verify_webhook_request(
        verify_token="tok", body={"token": "tok"},
    ) is True
    assert verify_webhook_request(
        verify_token="tok", body={"token": "xxx"},
    ) is False
    assert verify_webhook_request(verify_token="tok", body={}) is False
