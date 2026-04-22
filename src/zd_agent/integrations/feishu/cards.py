"""Feishu interactive card builders. Pure functions returning JSON-ready dicts."""
from __future__ import annotations

from typing import Any


def ticket_done_card(*, ticket_id: str, summary: str, zendesk_url: str) -> dict[str, Any]:
    """🟢 工单处理完成。"""
    actions: list[dict[str, Any]] = []
    if zendesk_url:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看 Zendesk"},
            "url": zendesk_url, "type": "primary",
        })
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "重新处理"},
        "value": {"command": "reprocess", "ticket_id": ticket_id},
        "type": "default",
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🟢 工单 #{ticket_id} 处理完成"},
            "template": "green",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
            {"tag": "action", "actions": actions},
        ],
    }


def ticket_escalate_card(*, ticket_id: str, reason: str, zendesk_url: str) -> dict[str, Any]:
    """⚠️ 工单需要人工介入。"""
    actions: list[dict[str, Any]] = []
    if zendesk_url:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看工单"},
            "url": zendesk_url, "type": "primary",
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"⚠️ 工单 #{ticket_id} 需要人工介入"},
            "template": "orange",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**原因**: {reason}"}},
            {"tag": "action", "actions": actions},
        ],
    }


def auth_renew_card(*, url: str, user: str) -> dict[str, Any]:
    """🔐 Teleport 认证快过期，请点击续期。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔐 Teleport 证书需要续期"},
            "template": "red",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content":
                f"用户 `{user}` 的 Teleport 证书即将过期。点击下方链接完成认证（约 10 秒）。"}},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "点击认证"},
                 "url": url, "type": "primary"},
            ]},
        ],
    }


def daily_summary_card(*, date: str, stats: dict[str, Any]) -> dict[str, Any]:
    """📊 每日报告。"""
    content = (
        f"**昨日处理工单**: {stats.get('total', 0)}\n"
        f"├─ 成功: {stats.get('success', 0)}\n"
        f"├─ 需人工介入: {stats.get('escalated', 0)}\n"
        f"└─ 失败: {stats.get('failed', 0)}\n\n"
        f"**退款工单**: {stats.get('refunds', 0)}\n"
        f"**未决工单**（等客户）: {stats.get('pending_customer', 0)}\n\n"
        f"**Token 消耗**: ~{stats.get('tokens', 0):,}\n"
        f"**Teleport 剩余**: {stats.get('teleport_remaining', 'N/A')}"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📊 zd-agent 每日报告 · {date}"},
            "template": "blue",
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
    }


def alert_card(*, title: str, message: str, urgent: bool = False) -> dict[str, Any]:
    """⚠️/🚨 通用告警。"""
    template = "red" if urgent else "yellow"
    prefix = "🚨 " if urgent else "⚠️ "
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": prefix + title},
            "template": template,
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": message}}],
    }
