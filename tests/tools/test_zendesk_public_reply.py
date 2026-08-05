"""Unit tests for ZendeskPublicReply tool.

Covers:
- footer 是否按 reply_type 正确追加（4 个 variant）
- footer 幂等性（LLM 不小心写了 footer 不重复加）
- kill switch（cfg.public_reply.enabled=false 时直接失败）
- 参数校验（ticket_id 类型 / body 空 / reply_type enum）
- dry_run 模式（不真发，存草稿）
- 真发送（mock browser relay）
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig
from zd_agent.tools.zendesk_public_reply import (
    _FOOTER_BY_TYPE,
    _FOOTER_STANDARD,
    _append_footer,
    _check_freshness_before_relay,
    _format_reply_body,
    _send_public_reply_safely,
    _VALID_REPLY_TYPES,
    send_public_reply,
    ZendeskPublicReply,
)
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
from zd_agent.services.public_reply_handoff_effects import PostPublicHandoffStore
from zd_agent.tools.public_reply_delivery import (
    PublicReplyTransportError,
    recent_duplicate_public_reply_exists,
)


@pytest.fixture(autouse=True)
def _available_duplicate_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
        AsyncMock(return_value={"success": True, "data": {"comments": []}}),
    )
    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        AsyncMock(return_value=([], {"id": 42})),
    )
    store = PublicReplyDeliveryStore(tmp_path / "public_reply_outbox.sqlite3")
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply._delivery_store_for_config",
        lambda _cfg: store,
    )
    handoff_store = PostPublicHandoffStore(
        tmp_path / "post_public_handoff_effects.sqlite3"
    )
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply._handoff_store_for_config",
        lambda _cfg: handoff_store,
    )


@pytest.mark.asyncio
async def test_duplicate_lookup_follows_all_conversation_pages() -> None:
    body = "A frozen customer-visible reply"
    caller = AsyncMock(
        side_effect=[
            {
                "success": True,
                "data": {
                    "comments": [],
                    "conversation_log": {
                        "events": [],
                        "meta": {"has_more": True, "after_cursor": "p2"},
                    },
                },
            },
            {
                "success": True,
                "data": {
                    "events": [
                        {
                            "author": {"type": "agent"},
                            "content": {"text": body},
                            "created_at": "2026-07-16T00:00:00Z",
                        },
                    ],
                    "meta": {"has_more": False},
                },
            },
        ]
    )

    assert await recent_duplicate_public_reply_exists(
        ticket_id=42,
        body_with_footer=body,
        not_before=0,
        zendesk_tool_caller=caller,
    )
    assert [call.args[0] for call in caller.await_args_list] == [
        "get_ticket_comments", "get_ticket_conversation_log",
    ]


@pytest.mark.asyncio
async def test_duplicate_lookup_fails_closed_on_incomplete_conversation() -> None:
    caller = AsyncMock(return_value={
        "success": True,
        "data": {
            "comments": [],
            "conversation_log": {
                "events": [],
                "meta": {"has_more": True, "after_cursor": ""},
            },
        },
    })

    with pytest.raises(PublicReplyTransportError, match="cursor incomplete"):
        await recent_duplicate_public_reply_exists(
            ticket_id=42,
            body_with_footer="reply",
            not_before=0,
            zendesk_tool_caller=caller,
        )


@pytest.mark.asyncio
async def test_relay_success_with_delivery_mark_failure_stays_no_resend(tmp_path) -> None:
    store = PublicReplyDeliveryStore(tmp_path / "outbox.sqlite3")
    store.mark_delivered = MagicMock(return_value=False)  # type: ignore[method-assign]
    relay = AsyncMock()
    remote = AsyncMock(return_value={"success": True, "data": {"comments": []}})

    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_make_cfg(relay=True),
    ), patch(
        "zd_agent.tools.zendesk_public_reply.call_zendesk_tool", remote,
    ), patch(
        "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply", relay,
    ), pytest.raises(PublicReplyTransportError, match="claim expired"):
        await _send_public_reply_safely(
            ticket_id=42,
            body_with_footer="Frozen body",
            reply_type="handoff",
            conversation_id="conv-1",
            delivery_attempt_id="mark-failure",
            delivery_store=store,
        )

    attempt = store.get(ticket_id=42, delivery_attempt_id="mark-failure")
    assert attempt is not None and attempt.ambiguous
    assert store.claim(attempt.delivery_key) is None


# ---------- footer helpers ----------

class TestFooterAppend:
    def test_tool_description_explains_automatic_handoff_hours(self) -> None:
        assert "上海时间 09:00（含）到 18:00（不含）" in ZendeskPublicReply.description
        assert "不要在 body 里重复写工作时间" in ZendeskPublicReply.description

    def test_append_standard_footer_for_ask(self) -> None:
        out = _append_footer("Hello world", "ask")
        # 身份标识必须有（合规），但不使用 emoji / Markdown 分隔线制造噪音。
        assert "Dify AI Support" in out
        assert "This reply was sent by Dify AI Support." in out
        # 仍保留 'human' 关键词（trigger_decider fast-path 检测要靠它）
        assert '"human"' in out
        assert "support engineer" in out
        assert "🤖" not in out
        assert "---" not in out
        assert out.startswith("Hello world\n\u200b\nThis reply was sent")

    def test_append_solution_uses_same_standard_footer(self) -> None:
        out_ask = _append_footer("body", "ask")
        out_solution = _append_footer("body", "solution")
        assert out_ask == out_solution

    def test_append_handoff_footer_confirms_human_followup(self) -> None:
        out = _append_footer(
            "Notified human teammate.",
            "handoff",
            now=datetime(2026, 7, 14, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        assert "This reply was sent by Dify AI Support." in out
        assert "A human support engineer will follow up in this ticket." in out
        assert 'Reply "human"' not in out

    @pytest.mark.parametrize(
        ("hour", "minute"),
        [(9, 0), (17, 59)],
    )
    def test_handoff_during_shanghai_support_hours(
        self,
        hour: int,
        minute: int,
    ) -> None:
        out = _append_footer(
            "A support engineer will review this case.",
            "handoff",
            now=datetime(
                2026,
                7,
                14,
                hour,
                minute,
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ),
        )

        assert "9:00 AM to 6:00 PM Shanghai time (UTC+8)" in out
        assert "submitted during this window" in out
        assert "reply as soon as possible" in out
        assert "next support window" not in out

    @pytest.mark.parametrize(
        ("hour", "minute"),
        [(8, 59), (18, 0)],
    )
    def test_handoff_outside_shanghai_support_hours(
        self,
        hour: int,
        minute: int,
    ) -> None:
        out = _append_footer(
            "A support engineer will review this case.",
            "handoff",
            now=datetime(
                2026,
                7,
                14,
                hour,
                minute,
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ),
        )

        assert "9:00 AM to 6:00 PM Shanghai time (UTC+8)" in out
        assert "currently outside these hours" in out
        assert "next support window begins" in out
        assert "do not need to open another ticket" in out

    def test_handoff_converts_utc_to_shanghai_time(self) -> None:
        out = _append_footer(
            "A support engineer will review this case.",
            "handoff",
            now=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        )

        assert "submitted during this window" in out

    def test_append_closed_footer_mentions_reopen(self) -> None:
        out = _append_footer("Glad it's resolved!", "closed")
        assert "Reply here or open a new ticket if the issue returns." in out
        assert "reply with" not in out

    def test_idempotent_if_body_already_has_footer(self) -> None:
        """LLM 不小心写了 footer 也不重复追加。"""
        body_with_footer = "Hello" + _FOOTER_STANDARD
        out = _append_footer(body_with_footer, "ask")
        assert out.count("Dify AI Support") == 1

    def test_all_reply_types_covered(self) -> None:
        assert _VALID_REPLY_TYPES == {"ask", "solution", "handoff", "closed"}
        for t in _VALID_REPLY_TYPES:
            assert t in _FOOTER_BY_TYPE


# ---------- .md URL post-process (#2645 修复) ----------

class TestStripDocsMdSuffix:
    def test_strips_md_from_docs_dify_ai(self) -> None:
        body = "See https://docs.dify.ai/api-reference/files/upload-file.md for details."
        out = _append_footer(body, "ask")
        assert "upload-file.md" not in out
        assert "upload-file" in out
        # 验证 URL 完整保留前缀
        assert "https://docs.dify.ai/api-reference/files/upload-file" in out

    def test_strips_multiple_docs_md_urls(self) -> None:
        body = (
            "Run Workflow: https://docs.dify.ai/api-reference/workflows/run-workflow.md\n"
            "Upload File: https://docs.dify.ai/api-reference/files/upload-file.md"
        )
        out = _append_footer(body, "ask")
        assert "run-workflow.md" not in out
        assert "upload-file.md" not in out
        assert "run-workflow\n" in out  # 后面跟换行说明 URL 被正确截断
        assert "upload-file\n" in out

    def test_does_not_strip_md_from_github_urls(self) -> None:
        """GitHub 的 .md URL 是合法的（README.md 等），不该被去掉"""
        body = "See github.com/langgenius/dify/blob/main/README.md"
        out = _append_footer(body, "ask")
        assert "README.md" in out

    def test_no_docs_url_no_change(self) -> None:
        """body 里没 docs URL 时不影响内容"""
        body = "Hello, please check your config."
        out = _append_footer(body, "ask")
        assert "Hello, please check your config." in out

    def test_strip_works_in_real_world_reply(self) -> None:
        """模拟 #2645 实际 case：body 含 2 个 docs .md URL"""
        body = (
            "Use the exact `id` returned by `POST /v1/files/upload`.\n\n"
            "Run Workflow: https://docs.dify.ai/api-reference/workflows/run-workflow.md\n"
            "Upload File: https://docs.dify.ai/api-reference/files/upload-file.md\n\n"
            "Best regards,\nDify Support"
        )
        out = _append_footer(body, "solution")
        # 两个 .md 都被去掉
        assert ".md" not in out.split("Dify SaaS Agent")[0]  # body 部分无 .md
        # URL 主体仍在
        assert "run-workflow" in out
        assert "upload-file" in out


class TestReplyBodyFormatting:
    def test_removes_markdown_decoration_and_keeps_readable_sections(self) -> None:
        body = (
            "## **Error mechanism confirmed**\n"
            "The `PluginInvokeError` came from the **serverless runtime**.\n"
            "💡 Recommended next steps\n"
            "1. Create a new `conversation_id`.\n"
            "2. Retry the request."
        )

        out = _format_reply_body(body)

        assert out == (
            "Error mechanism confirmed:\n\n"
            "The PluginInvokeError came from the serverless runtime.\n\n"
            "Recommended next steps:\n\n"
            "1. Create a new conversation_id.\n"
            "2. Retry the request."
        )
        assert "**" not in out
        assert "`" not in out
        assert "💡" not in out

    def test_adds_blank_lines_around_sections_lists_and_signature(self) -> None:
        body = (
            "Hi cg,\n"
            "I checked the backend logs for the timestamps you shared.\n"
            "Evidence from your app `客服工具`:\n"
            "- 2026-06-10 02:57:46 UTC run `522f5006...`: failed after `604.277s`.\n"
            "- 2026-06-10 03:07:49 UTC run `8a778c32...`: failed after `609.042s`.\n"
            "Recommended next steps:\n"
            "1. In the LLM node, switch from the current plugin.\n"
            "2. Reduce the context passed into the LLM node.\n"
            "Best regards,\n"
            "Dify Support"
        )

        out = _format_reply_body(body)

        assert "Hi cg,\n\nI checked" in out
        assert "Evidence from your app 客服工具:\n\n- 2026" in out
        assert "609.042s.\n\nRecommended next steps:" in out
        assert "Recommended next steps:\n\n1. In the LLM node" in out
        assert "2. Reduce the context passed into the LLM node.\n\nBest regards," in out
        assert "Best regards,\nDify Support" in out

    def test_append_footer_uses_formatted_body(self) -> None:
        out = _append_footer(
            "Hi cg,\nI checked it.\nRecommended next steps:\n1. Retry.\nBest regards,\nDify Support",
            "ask",
        )

        assert "Hi cg,\n\u200b\nI checked it." in out
        assert "Recommended next steps:\n\u200b\n1. Retry." in out
        assert "Dify Support\n\u200b\nThis reply was sent by Dify AI Support." in out

    def test_append_footer_preserves_visual_blank_lines_for_messaging(self) -> None:
        out = _append_footer(
            "Hi cg,\n\nI checked it.\n\nEvidence:\n\n- One\n\nBest regards,\nDify Support",
            "solution",
        )

        assert "\n\n" not in out
        assert "Hi cg,\n\u200b\nI checked it." in out
        assert "Evidence:\n\u200b\n- One" in out
        assert "- One\n\u200b\nBest regards," in out


# ---------- tool execute() ----------

def _make_cfg(enabled: bool = True, relay: bool = False) -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=enabled)
    if relay:
        cfg.public_reply.browser_relay_url = "http://127.0.0.1:8097/mcp"
        cfg.public_reply.browser_relay_api_key = "test-relay-key"
        cfg.public_reply.browser_relay_timeout_seconds = 12
    return cfg


def _make_session(dry_run: bool = False) -> MagicMock:
    s = MagicMock()
    s.ticket_id = 42
    s.dry_run = dry_run
    s.reply_draft = ""
    s.reply_sent = False
    s.ticket_type = ""
    s.tool_call_log = []
    s.customer_msgs_snapshot_count = 0
    s.customer_msgs_snapshot_last_id = ""
    s.customer_msgs_snapshot_captured = True
    s.delivery_attempt_id = "test-delivery-attempt"
    s.session_id = "test-session"
    return s


def _fast_evidence_ledger(
    source_tool: str,
    *,
    claim: str = "The current product state supports this guidance.",
    role: str = "product_fact",
) -> dict:
    return {
        "route": "fast",
        "decision_scope": "customer_guidance",
        "root_cause_status": "not_applicable",
        "root_cause_summary": "",
        "rows": [
            {
                "claim": claim,
                "causal_role": role,
                "status": "confirmed",
                "source_tools": [source_tool],
                "object_identifiers": "ticket=42",
                "time_window": "checked_at=2026-07-29T00:00:00Z",
                "observed_result": "The successful tool result confirmed the claim.",
                "alternatives_checked": "Checked the current authoritative source.",
            },
        ],
    }


class TestExecute:
    @pytest.mark.asyncio
    async def test_kill_switch_blocks_send(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session()
        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(enabled=False),
        ):
            r = await tool.execute(
                {"ticket_id": 42, "body": "test", "reply_type": "ask"}, sess,
            )
        assert not r.success
        assert "kill switch" in r.error.lower() or "disabled" in r.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_ticket_id_rejected(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session()
        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {"body": "test", "reply_type": "ask"}, sess,
            )
        assert not r.success
        assert r.error == "ticket_id is required"

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {"ticket_id": "abc", "body": "test", "reply_type": "ask"}, sess,
            )
        assert not r.success
        assert "ticket_id" in r.error

    @pytest.mark.asyncio
    async def test_cross_ticket_public_reply_is_rejected(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            result = await tool.execute(
                {
                    "ticket_id": sess.ticket_id + 1,
                    "body": "A support engineer will follow up.",
                    "reply_type": "handoff",
                    "conversation_id": "conversation-other-ticket",
                },
                sess,
            )

        assert result.success is False
        assert result.error == "ticket_id does not match the active agent session"

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {"ticket_id": True, "body": "test", "reply_type": "ask"}, sess,
            )
        assert not r.success
        assert "ticket_id" in r.error

    @pytest.mark.asyncio
    async def test_invalid_string_args_rejected_before_strip(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=True)
        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {"ticket_id": 42, "body": ["hi"], "reply_type": "ask"}, sess,
            )
        assert not r.success
        assert "body" in r.error

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "hi",
                    "reply_type": "ask",
                    "conversation_id": 123,
                },
                sess,
            )
        assert not r.success
        assert "conversation_id" in r.error

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "hi",
                    "reply_type": "ask",
                    "messaging_app_id": 123,
                },
                sess,
            )
        assert not r.success
        assert "messaging_app_id" in r.error

    @pytest.mark.asyncio
    async def test_missing_required_text_args_are_explicit(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=True)
        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {"ticket_id": 42, "reply_type": "ask"}, sess,
            )
        assert not r.success
        assert r.error == "body is required"

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {"ticket_id": 42, "body": "hi"}, sess,
            )
        assert not r.success
        assert r.error == "reply_type is required"

    @pytest.mark.asyncio
    async def test_empty_body_rejected(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session()
        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {"ticket_id": 42, "body": "   ", "reply_type": "ask"}, sess,
            )
        assert not r.success
        assert "empty" in r.error

    @pytest.mark.asyncio
    async def test_invalid_reply_type_rejected(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session()
        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {"ticket_id": 42, "body": "hi", "reply_type": "rude"}, sess,
            )
        assert not r.success
        assert "reply_type" in r.error

    @pytest.mark.asyncio
    async def test_dry_run_saves_draft_no_send(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=True)
        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Hi customer",
                    "reply_type": "ask",
                    "internal_analysis": "The customer needs to clarify the request.",
                },
                sess,
            )
        assert r.success
        assert "Dry Run" in r.output
        assert "Dify AI Support" in sess.reply_draft  # footer 追加了
        assert sess.reply_sent is False

    @pytest.mark.asyncio
    async def test_execute_rejects_duplicate_public_reply_in_same_session(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.reply_sent = True
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Hi there",
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "internal_analysis": "The latest customer context was reviewed.",
                },
                sess,
            )

        assert not r.success
        assert "already sent" in r.error
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_blocks_reply_until_queued_images_are_injected(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.image_urls = [{
            "url": "data:image/png;base64,iVBORw0KGgoAAAAAAAAAAAAAAAAAAAAA",
            "file_name": "screenshot.png",
        }]
        sess.images_injected = False
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            result = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Here is the conclusion.",
                    "reply_type": "solution",
                    "conversation_id": "conv-1",
                },
                sess,
            )

        assert result.success is False
        assert "queued ticket images" in result.error
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_treats_matching_existing_reply_as_delivered(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        mock_relay = AsyncMock()
        mock_mcp = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "conversation_log": {
                        "events": [
                            {
                                "author": {"type": "agent"},
                                "content": {"text": _append_footer("Hi there", "ask")},
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            }
                        ]
                    }
                },
            },
        )

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
            mock_mcp,
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Hi there",
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "internal_analysis": "The current ticket context was reviewed.",
                },
                sess,
            )

        assert r.success
        assert "already delivered" in r.output
        assert sess.reply_sent is True
        assert mock_mcp.await_count == 3
        assert [call.args[0] for call in mock_mcp.await_args_list] == [
            "get_ticket_comments",
            "get_ticket_comments",
            "add_internal_note",
        ]
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_public_reply_blocks_unsupported_concrete_claims(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.tool_call_log = [
            {
                "tool": "github_search_prs",
                "args": {"query": "timeout fix"},
                "success": True,
                "error": None,
                "result_preview": "No matching PR found",
            },
        ]
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "This was fixed in PR #99999.",
                    "reply_type": "solution",
                    "conversation_id": "conv-1",
                },
                sess,
            )

        assert not r.success
        assert "evidence hard gate" in r.error
        assert "#99999" in r.error
        assert sess.reply_sent is False
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_public_reply_allows_concrete_claims_seen_in_tools(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.tool_call_log = [
            {
                "tool": "github_search_prs",
                "args": {"query": "timeout fix"},
                "success": True,
                "error": None,
                "result_preview": "Found PR #33216 fixing the timeout regression",
            },
        ]
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "This was fixed in PR #33216.",
                    "reply_type": "solution",
                    "conversation_id": "conv-1",
                    "internal_analysis": "Verified the matching GitHub change.",
                    "evidence_ledger": _fast_evidence_ledger(
                        "github_search_prs",
                        claim="PR #33216 contains the matching timeout fix.",
                    ),
                },
                sess,
            )

        assert r.success
        mock_relay.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_public_reply_blocks_internal_source_paths_even_if_seen_in_tools(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.tool_call_log = [
            {
                "tool": "github_code_search",
                "args": {"query": "Jina rerank"},
                "success": True,
                "error": None,
                "result_preview": (
                    "models/jina/models/rerank/rerank.py and "
                    "models/jina/models/rerank/jina-reranker-m0.yaml"
                ),
            },
        ]
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": (
                        "Jina rerank support exists in "
                        "`models/jina/models/rerank/rerank.py` and "
                        "`models/jina/models/rerank/jina-reranker-m0.yaml`."
                    ),
                    "reply_type": "solution",
                    "conversation_id": "conv-1",
                },
                sess,
            )

        assert not r.success
        assert "privacy hard gate" in r.error
        assert "internal source path" in r.error
        assert sess.reply_sent is False
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refund_reply_blocks_eligibility_without_owner_gate(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.ticket_type = "refund"
        sess.tool_call_log = []
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": (
                        "Good news — your request qualifies for a partial refund "
                        "under our refund policy."
                    ),
                    "reply_type": "solution",
                    "conversation_id": "conv-1",
                },
                sess,
            )

        assert not r.success
        assert "Refund owner gate" in r.error
        assert sess.reply_sent is False
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handoff_blocks_current_plan_claim_from_inspector_all(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=True)
        sess.ticket_type = "billing"
        sess.tool_call_log = [
            {
                "tool": "inspector_exec",
                "args": {
                    "email": "customer@example.com",
                    "section": "all",
                },
                "success": True,
                "error": None,
                "result_preview": "TENANTS (1)\nname,plan\nWorkspace,basic",
            },
        ]

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            result = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": (
                        "The workspace associated with this request is currently "
                        "recorded on the Basic plan."
                    ),
                    "reply_type": "handoff",
                    "internal_analysis": "Inspector all showed a Prod plan value.",
                },
                sess,
            )

        assert result.success is False
        assert "Billing plan evidence gate" in (result.error or "")
        assert "section=billing" in (result.error or "")
        assert sess.reply_sent is False

    @pytest.mark.asyncio
    async def test_handoff_allows_matching_current_plan_from_billing_section(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=True)
        sess.ticket_type = "billing"
        sess.tool_call_log = [
            {
                "tool": "inspector_exec",
                "args": {
                    "tenant_id": "tenant-1",
                    "section": "billing",
                },
                "success": True,
                "error": None,
                "result_preview": (
                    "AUTHORITATIVE_BILLING_PLAN=team\n"
                    "AUTHORITATIVE_BILLING_STATUS=active\n"
                    "AUTHORITATIVE_BILLING_SOURCE=billing_db"
                ),
            },
        ]

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(),
        ):
            result = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Your workspace is currently on the Team plan.",
                    "reply_type": "handoff",
                    "internal_analysis": (
                        "The tenant-scoped billing section confirmed Team."
                    ),
                },
                sess,
            )

        assert result.success is True
        assert "Dry Run" in result.output
        assert sess.reply_sent is False

    @pytest.mark.asyncio
    async def test_refund_reply_allows_owner_confirmation_ask_without_gate(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.ticket_type = "refund"
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": (
                        "Please ask the workspace owner account to confirm this "
                        "refund request before we review eligibility."
                    ),
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "internal_analysis": "Requester authority is not confirmed.",
                },
                sess,
            )

        assert r.success
        mock_relay.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refund_reply_requires_human_decision_after_owner_gate(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.ticket_type = "refund"
        sess.tool_call_log = [
            {
                "tool": "sql_query",
                "args": {
                    "database": "prod",
                    "sql": (
                        "SELECT taj.role, a.email FROM tenant_account_joins taj "
                        "JOIN accounts a ON a.id = taj.account_id "
                        "WHERE taj.tenant_id = 'wid'"
                    ),
                },
                "success": True,
                "error": None,
                "result_preview": "Rows: 1\nrole,email\nowner,user@example.com",
            },
        ]
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Your request qualifies for a partial refund.",
                    "reply_type": "solution",
                    "conversation_id": "conv-1",
                },
                sess,
            )

        assert not r.success
        assert "Refund decision gate" in (r.error or "")
        assert "human business approval" in (r.error or "")
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_conversation_id_does_not_fall_back_to_public_comment(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)

        mock_client = MagicMock()
        mock_client.reply_ticket = MagicMock(return_value={"ticket": {"id": 1}})

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.get_messaging_context",
            return_value={},
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Hi there",
                    "reply_type": "ask",
                    "internal_analysis": "The ticket was reviewed before replying.",
                },
                sess,
            )

        assert not r.success
        assert "conversation_id" in r.error
        assert sess.reply_sent is False
        mock_client.reply_ticket.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_public_reply_aborts_before_relay_on_new_customer_message(self) -> None:
        mock_relay = AsyncMock()

        async def _fetch_customer_messages(ticket_id: int):
            return [
                {"id": "m1", "body": "Name: Alice"},
                {
                    "id": "m2",
                    "body": "I was charged $59 after cancelling my subscription.",
                },
            ], {}

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.services.ticket_messages._fetch_customer_messages",
            side_effect=_fetch_customer_messages,
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            success, msg = await send_public_reply(
                ticket_id=42,
                body="Please share more details.",
                reply_type="ask",
                conversation_id="conv-1",
                snapshot_customer_message_count=1,
                snapshot_customer_last_message_id="m1",
            )

        assert success is False
        assert "freshness check abort" in msg
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_public_reply_rejects_invalid_ticket_id_before_context_lookup(self) -> None:
        mock_context = MagicMock(return_value={"conversation_id": "conv-1"})
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.get_messaging_context",
            mock_context,
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            success, msg = await send_public_reply(
                ticket_id=0,
                body="Please share more details.",
                reply_type="ask",
            )

        assert success is False
        assert msg == "ticket_id must be a positive integer"
        mock_context.assert_not_called()
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_public_reply_treats_matching_comment_as_delivered(self) -> None:
        mock_relay = AsyncMock()
        mock_mcp = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "comments": [
                        {
                            "public": True,
                            "plain_body": _append_footer(
                                "Please share more details.",
                                "ask",
                            ),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ]
                },
            },
        )

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
            mock_mcp,
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ), patch(
            "zd_agent.tools.zendesk_public_reply._check_freshness_before_relay",
            AsyncMock(return_value=MagicMock(continue_send=True)),
        ):
            success, msg = await send_public_reply(
                ticket_id=42,
                body="Please share more details.",
                reply_type="ask",
                conversation_id="conv-1",
                snapshot_customer_message_count=0,
                delivery_attempt_id="direct-attempt-42",
            )

        assert success is True
        assert "already delivered" in msg
        assert mock_mcp.await_count == 3
        assert [call.args[0] for call in mock_mcp.await_args_list] == [
            "get_ticket_comments",
            "get_ticket_comments",
            "add_internal_note",
        ]
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_public_reply_fails_closed_when_duplicate_check_unavailable(
        self,
    ) -> None:
        mock_relay = AsyncMock()
        mock_mcp = AsyncMock(return_value={
            "success": False,
            "code": "ZENDESK_MCP_REQUEST_FAILED",
            "error": "connection refused",
        })

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
            mock_mcp,
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ), patch(
            "zd_agent.tools.zendesk_public_reply._check_freshness_before_relay",
            AsyncMock(return_value=MagicMock(continue_send=True)),
        ):
            success, msg = await send_public_reply(
                ticket_id=42,
                body="Please share more details.",
                reply_type="ask",
                conversation_id="conv-1",
                snapshot_customer_message_count=0,
                delivery_attempt_id="direct-attempt-42",
            )

        assert success is False
        assert "duplicate check unavailable" in msg.lower()
        mock_mcp.assert_awaited_once()
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_direct_ask_requires_customer_snapshot(self) -> None:
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            success, message = await send_public_reply(
                ticket_id=42,
                body="Please share more details.",
                reply_type="ask",
                conversation_id="conv-1",
            )

        assert success is False
        assert "snapshot is unavailable" in message
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_direct_ask_blocks_when_freshness_check_fails(self) -> None:
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply._check_freshness_before_relay",
            AsyncMock(side_effect=RuntimeError("zendesk unavailable")),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            success, message = await send_public_reply(
                ticket_id=42,
                body="Please share more details.",
                reply_type="ask",
                conversation_id="conv-1",
                snapshot_customer_message_count=1,
                snapshot_customer_last_message_id="message-1",
            )

        assert success is False
        assert "freshness check unavailable" in message
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_direct_freshness_blocks_when_ticket_fetch_returns_none(self) -> None:
        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.services.ticket_messages._fetch_customer_messages",
            AsyncMock(return_value=([], None)),
        ):
            result = await _check_freshness_before_relay(
                ticket_id=42,
                snapshot_count=0,
                snapshot_last_id=None,
            )

        assert result is not None
        assert result.continue_send is False
        assert result.reason == "fetch_error: RuntimeError"

    @pytest.mark.asyncio
    async def test_native_messaging_context_sends_browser_relay_message(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)

        mock_client = MagicMock()
        mock_client.reply_ticket = MagicMock(return_value={"ticket": {"id": 1}})
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Hi there",
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "messaging_app_id": "app-1",
                    "internal_analysis": "The current ticket context was reviewed.",
                },
                sess,
            )

        assert r.success
        assert sess.reply_sent is True
        mock_relay.assert_awaited_once()
        kwargs = mock_relay.await_args.kwargs
        assert kwargs["ticket_id"] == 42
        assert "Dify AI Support" in kwargs["body"]
        assert kwargs["url"] == "http://127.0.0.1:8097/mcp"
        assert kwargs["api_key"] == "test-relay-key"
        assert kwargs["timeout_seconds"] == 12
        # native messaging 不应退回 Support API ticket comment。
        mock_client.reply_ticket.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_public_reply_adds_internal_analysis_note(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.tool_call_log = [
            {
                "tool": "zendesk_get_ticket",
                "args": {"ticket_id": 42},
                "success": True,
                "error": None,
                "result_preview": "The latest customer retry succeeded.",
            },
        ]
        mock_relay = AsyncMock()
        mcp_calls: list[tuple[str, dict]] = []

        async def _call_mcp(name: str, arguments: dict):
            mcp_calls.append((name, arguments))
            if name == "get_ticket_comments":
                return {"success": True, "data": {"comments": []}}
            if name == "add_internal_note":
                return {"success": True, "data": {"comment_id": 123}}
            raise AssertionError(f"unexpected MCP tool: {name}")

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
            side_effect=_call_mcp,
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            result = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Hi Alex,\n\nPlease retry the request now.",
                    "reply_type": "solution",
                    "conversation_id": "conv-1",
                    "internal_analysis": (
                        "Confirmed the failed run recovered. The customer can retry; "
                        "monitor the next execution for recurrence."
                    ),
                    "evidence_ledger": _fast_evidence_ledger(
                        "zendesk_get_ticket",
                        claim="The latest customer retry succeeded.",
                        role="current_state",
                    ),
                },
                sess,
            )

        assert result.success
        note_calls = [args for name, args in mcp_calls if name == "add_internal_note"]
        assert len(note_calls) == 1
        note_body = note_calls[0]["body"]
        assert "## Internal Analysis" in note_body
        assert "Confirmed the failed run recovered." in note_body
        assert "Public reply delivered" in note_body
        assert "public-reply-analysis:" in note_body

    @pytest.mark.asyncio
    async def test_direct_public_reply_adds_default_internal_analysis_note(self) -> None:
        mock_relay = AsyncMock()
        mcp_calls: list[tuple[str, dict]] = []

        async def _call_mcp(name: str, arguments: dict):
            mcp_calls.append((name, arguments))
            if name == "get_ticket_comments":
                return {"success": True, "data": {"comments": []}}
            if name == "add_internal_note":
                return {"success": True, "data": {"comment_id": 456}}
            raise AssertionError(f"unexpected MCP tool: {name}")

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
            side_effect=_call_mcp,
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ), patch(
            "zd_agent.tools.zendesk_public_reply._check_freshness_before_relay",
            AsyncMock(return_value=MagicMock(continue_send=True)),
        ):
            success, _ = await send_public_reply(
                ticket_id=42,
                body="Please share the UTC timestamp of the latest failure.",
                reply_type="ask",
                conversation_id="conv-1",
                snapshot_customer_message_count=0,
                delivery_attempt_id="direct-note-attempt-42",
            )

        assert success
        note_calls = [args for name, args in mcp_calls if name == "add_internal_note"]
        assert len(note_calls) == 1
        assert "minimum missing details" in note_calls[0]["body"]

    @pytest.mark.asyncio
    async def test_saved_native_messaging_context_is_used_for_tool_call(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)

        mock_client = MagicMock()
        mock_client.reply_ticket = MagicMock(return_value={"ticket": {"id": 1}})
        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.get_messaging_context",
            return_value={"conversation_id": "conv-saved", "app_id": "app-saved"},
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Hi there",
                    "reply_type": "ask",
                    "internal_analysis": "The current ticket context was reviewed.",
                },
                sess,
            )

        assert r.success
        kwargs = mock_relay.await_args.kwargs
        assert kwargs["ticket_id"] == 42
        mock_client.reply_ticket.assert_not_called()

    @pytest.mark.asyncio
    async def test_native_messaging_relay_failure_does_not_fall_back_to_comment(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)

        mock_client = MagicMock()
        mock_client.reply_ticket = MagicMock(return_value={"ticket": {"id": 1}})
        mock_relay = AsyncMock(side_effect=RuntimeError("login_required"))

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "Hi there",
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "internal_analysis": "The current ticket context was reviewed.",
                },
                sess,
            )

        assert not r.success
        assert "login_required" in r.error
        assert sess.reply_sent is False
        mock_client.reply_ticket.assert_not_called()

class TestPreSendCheckIntegration:
    """M5: ZendeskPublicReply 接入 pre_send_check（snapshot > 0 时调）"""

    @pytest.mark.parametrize("reply_type", ["ask", "closed"])
    @pytest.mark.asyncio
    async def test_missing_snapshot_blocks_resolvable_reply(
        self,
        reply_type: str,
    ) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.customer_msgs_snapshot_captured = False

        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "test",
                    "reply_type": reply_type,
                    "conversation_id": "conv-1",
                    "internal_analysis": "The customer message snapshot is required.",
                },
                sess,
            )
        assert not r.success
        assert "snapshot is unavailable" in r.error
        mock_relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handoff_skips_pre_send_check_even_with_snapshot(self) -> None:
        """handoff 类型跳过 freshness check（已经决定转人工了）"""
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.customer_msgs_snapshot_count = 5  # 即使有 snapshot

        mock_relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            mock_relay,
        ), patch(
            "zd_agent.runtime.pre_send_check.pre_send_check_or_abort",
            new_callable=lambda: pytest.fail,  # 不应被调
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "transferred",
                    "reply_type": "handoff",
                    "conversation_id": "conv-1",
                    "internal_analysis": "The unresolved case is being handed off.",
                },
                sess,
            )
        assert r.success

    @pytest.mark.asyncio
    async def test_ask_with_snapshot_aborts_on_substantive_new_msg(self) -> None:
        """ask + snapshot 设了 + freshness check 返回 abort → tool 返回失败"""
        from zd_agent.runtime.pre_send_check import FreshnessResult

        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.customer_msgs_snapshot_count = 1
        sess.customer_msgs_snapshot_last_id = "m1"

        async def _abort_check(**kwargs):
            return FreshnessResult(
                continue_send=False,
                reason="substantive_heuristic",
                new_messages_count=1,
            )

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.runtime.pre_send_check.pre_send_check_or_abort",
            side_effect=_abort_check,
        ), patch(
            "zd_agent.services.ticket_messages._fetch_customer_messages",
            new_callable=lambda: (lambda tid: ([], {})),
        ):
            r = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "ask",
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "internal_analysis": "Waiting for the latest customer context.",
                },
                sess,
            )
        assert not r.success
        assert "Pre-send check abort" in r.error
        assert "substantive_heuristic" in r.error

    @pytest.mark.asyncio
    async def test_accepted_non_substantive_messages_advance_session_cursor(
        self,
    ) -> None:
        """A later webhook must not reprocess messages the send gate accepted."""
        from zd_agent.runtime.pre_send_check import (
            FreshnessRecovery,
            FreshnessRecoveryAction,
            FreshnessResult,
        )

        tool = ZendeskPublicReply()
        session = _make_session(dry_run=False)
        session.customer_msgs_snapshot_count = 1
        session.customer_msgs_snapshot_last_id = "m1"
        relay = AsyncMock()

        async def _accepted_check(**kwargs):
            return FreshnessResult(
                continue_send=True,
                reason="all_new_msgs_non_substantive",
                new_messages_count=1,
                recovery=FreshnessRecovery(
                    action=FreshnessRecoveryAction.REFRESH_CONTEXT,
                    next_snapshot_count=2,
                    next_snapshot_last_id="m2",
                ),
            )

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.runtime.pre_send_check.pre_send_check_or_abort",
            side_effect=_accepted_check,
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            relay,
        ):
            result = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "We are checking this for you.",
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "internal_analysis": "The latest customer context was reviewed.",
                },
                session,
            )

        assert result.success
        assert session.customer_msgs_snapshot_count == 2
        assert session.customer_msgs_snapshot_last_id == "m2"

    @pytest.mark.asyncio
    async def test_ask_blocks_when_freshness_check_fails(self) -> None:
        tool = ZendeskPublicReply()
        sess = _make_session(dry_run=False)
        sess.customer_msgs_snapshot_count = 1
        sess.customer_msgs_snapshot_last_id = "m1"

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.runtime.pre_send_check.pre_send_check_or_abort",
            side_effect=RuntimeError("zendesk unavailable"),
        ), patch(
            "zd_agent.services.ticket_messages._fetch_customer_messages",
            new_callable=lambda: (lambda ticket_id: ([], {})),
        ):
            result = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "ask",
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "internal_analysis": "The freshness check must succeed first.",
                },
                sess,
            )

        assert not result.success
        assert "freshness check unavailable" in result.error

    @pytest.mark.asyncio
    async def test_ask_blocks_when_ticket_fetch_returns_none(self) -> None:
        tool = ZendeskPublicReply()
        session = _make_session(dry_run=False)
        relay = AsyncMock()

        with patch(
            "zd_agent.tools.zendesk_public_reply.get_config",
            return_value=_make_cfg(relay=True),
        ), patch(
            "zd_agent.services.ticket_messages._fetch_customer_messages",
            AsyncMock(return_value=([], None)),
        ), patch(
            "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
            relay,
        ):
            result = await tool.execute(
                {
                    "ticket_id": 42,
                    "body": "ask",
                    "reply_type": "ask",
                    "conversation_id": "conv-1",
                    "internal_analysis": "The latest Zendesk ticket fetch is required.",
                },
                session,
            )

        assert not result.success
        assert "freshness check unavailable" in result.error
        relay.assert_not_awaited()


@pytest.mark.asyncio
async def test_historical_matching_body_does_not_suppress_new_attempt(tmp_path) -> None:
    body = _append_footer("Please share details.", "ask")
    store = PublicReplyDeliveryStore(tmp_path / "outbox.sqlite3")
    relay = AsyncMock()
    historical = AsyncMock(return_value={
        "success": True,
        "data": {
            "comments": [{
                "public": True,
                "plain_body": body,
                "created_at": "2020-01-01T00:00:00Z",
            }],
        },
    })

    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_make_cfg(relay=True),
    ), patch(
        "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
        historical,
    ), patch(
        "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
        relay,
    ):
        sent_now = await _send_public_reply_safely(
            ticket_id=42,
            body_with_footer=body,
            reply_type="ask",
            conversation_id="conv-1",
            delivery_attempt_id="attempt-new",
            delivery_store=store,
        )

    assert sent_now is True
    relay.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_reuses_first_body_and_confirms_ambiguous_delivery(tmp_path) -> None:
    first_body = _append_footer("First generated body.", "ask")
    second_body = _append_footer("Changed retry body.", "ask")
    clock = {"now": 100.0}
    store = PublicReplyDeliveryStore(
        tmp_path / "outbox.sqlite3",
        claim_lease_seconds=5,
        now_fn=lambda: clock["now"],
    )
    relay = AsyncMock(side_effect=RuntimeError("relay result unknown"))
    remote = AsyncMock(return_value={"success": True, "data": {"comments": []}})

    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_make_cfg(relay=True),
    ), patch(
        "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
        remote,
    ), patch(
        "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
        relay,
    ):
        with pytest.raises(RuntimeError, match="unknown"):
            await _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=first_body,
                reply_type="ask",
                conversation_id="conv-1",
                delivery_attempt_id="attempt-retry",
                delivery_store=store,
            )

        with pytest.raises(PublicReplyTransportError, match="manual reconciliation"):
            await _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=second_body,
                reply_type="ask",
                conversation_id="conv-1",
                delivery_attempt_id="attempt-retry",
                delivery_store=store,
            )
        assert relay.await_count == 1

        clock["now"] += 6
        remote.return_value = {
            "success": True,
            "data": {
                "comments": [{
                    "public": True,
                    "plain_body": first_body,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }],
            },
        }
        delivered = await _send_public_reply_safely(
            ticket_id=42,
            body_with_footer=second_body,
            reply_type="ask",
            conversation_id="conv-1",
            delivery_attempt_id="attempt-retry",
            delivery_store=store,
        )

    assert delivered is False
    assert relay.await_count == 1


@pytest.mark.asyncio
async def test_ambiguous_retry_uses_snapshot_that_generated_frozen_body(tmp_path) -> None:
    first_body = _append_footer("Answer generated for message m1.", "solution")
    regenerated_body = _append_footer("Answer regenerated for message m2.", "solution")
    clock = {"now": 100.0}
    store = PublicReplyDeliveryStore(
        tmp_path / "outbox.sqlite3",
        claim_lease_seconds=1,
        now_fn=lambda: clock["now"],
    )
    relay = AsyncMock(side_effect=[RuntimeError("response lost"), None])
    remote = AsyncMock(return_value={"success": True, "data": {"comments": []}})

    cfg = _make_cfg(relay=True)
    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=cfg,
    ), patch(
        "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
        remote,
    ), patch(
        "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
        relay,
    ):
        with pytest.raises(RuntimeError, match="response lost"):
            await _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=first_body,
                reply_type="solution",
                conversation_id="conv-1",
                snapshot_customer_message_count=1,
                snapshot_customer_last_message_id="m1",
                delivery_attempt_id="attempt-snapshot",
                delivery_store=store,
            )

        clock["now"] += 2
        with pytest.raises(PublicReplyTransportError, match="manual reconciliation"):
            await _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=regenerated_body,
                reply_type="solution",
                conversation_id="conv-1",
                snapshot_customer_message_count=2,
                snapshot_customer_last_message_id="m2",
                delivery_attempt_id="attempt-snapshot",
                delivery_store=store,
            )

    assert relay.await_count == 1


@pytest.mark.asyncio
async def test_definite_unsent_relay_error_discards_frozen_attempt(tmp_path) -> None:
    from zd_agent.integrations.zendesk_browser_relay import (
        BrowserRelayDefiniteUnsentError,
    )

    first_body = _append_footer("Stale body A.", "handoff")
    replacement_body = _append_footer("Replacement body B.", "handoff")
    store = PublicReplyDeliveryStore(tmp_path / "outbox.sqlite3")
    relay = AsyncMock(
        side_effect=[
            BrowserRelayDefiniteUnsentError("login required before Send"),
            None,
        ]
    )

    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_make_cfg(relay=True),
    ), patch(
        "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
        AsyncMock(return_value={"success": True, "data": {"comments": []}}),
    ), patch(
        "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
        relay,
    ):
        with pytest.raises(BrowserRelayDefiniteUnsentError):
            await _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=first_body,
                reply_type="handoff",
                conversation_id="conv-1",
                delivery_attempt_id="definitely-unsent",
                delivery_store=store,
            )
        assert await _send_public_reply_safely(
            ticket_id=42,
            body_with_footer=replacement_body,
            reply_type="handoff",
            conversation_id="conv-1",
            delivery_attempt_id="definitely-unsent",
            delivery_store=store,
        ) is True

    assert relay.await_args_list[1].kwargs["body"] == replacement_body


@pytest.mark.asyncio
async def test_concurrent_same_attempt_sends_only_once(tmp_path) -> None:
    body = _append_footer("One delivery only.", "ask")
    store = PublicReplyDeliveryStore(tmp_path / "outbox.sqlite3")

    async def slow_relay(**_kwargs) -> None:
        await asyncio.sleep(0.05)

    relay = AsyncMock(side_effect=slow_relay)

    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_make_cfg(relay=True),
    ), patch(
        "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
        AsyncMock(return_value={"success": True, "data": {"comments": []}}),
    ), patch(
        "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
        relay,
    ):
        results = await asyncio.gather(
            _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=body,
                reply_type="ask",
                conversation_id="conv-1",
                delivery_attempt_id="same-attempt",
                delivery_store=store,
            ),
            _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=body,
                reply_type="ask",
                conversation_id="conv-1",
                delivery_attempt_id="same-attempt",
                delivery_store=store,
            ),
            return_exceptions=True,
        )

    assert relay.await_count == 1
    assert results.count(True) == 1
    assert any(isinstance(result, PublicReplyTransportError) for result in results)


@pytest.mark.asyncio
async def test_active_delivery_claim_is_renewed_while_relay_is_slow(tmp_path) -> None:
    body = _append_footer("One slow delivery only.", "handoff")
    store = PublicReplyDeliveryStore(
        tmp_path / "outbox.sqlite3",
        claim_lease_seconds=1,
    )
    relay_started = asyncio.Event()
    relay_calls = 0

    async def slow_relay(**_kwargs) -> None:
        nonlocal relay_calls
        relay_calls += 1
        relay_started.set()
        await asyncio.sleep(1.2)

    relay = AsyncMock(side_effect=slow_relay)
    remote = AsyncMock(return_value={"success": True, "data": {"comments": []}})

    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_make_cfg(relay=True),
    ), patch(
        "zd_agent.tools.zendesk_public_reply.call_zendesk_tool",
        remote,
    ), patch(
        "zd_agent.tools.zendesk_public_reply.send_browser_relay_reply",
        relay,
    ):
        first = asyncio.create_task(
            _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=body,
                reply_type="handoff",
                conversation_id="conv-1",
                delivery_attempt_id="slow-attempt",
                delivery_store=store,
            )
        )
        await relay_started.wait()
        await asyncio.sleep(1.05)
        second = await asyncio.gather(
            _send_public_reply_safely(
                ticket_id=42,
                body_with_footer=body,
                reply_type="handoff",
                conversation_id="conv-1",
                delivery_attempt_id="slow-attempt",
                delivery_store=store,
            ),
            return_exceptions=True,
        )

    assert await first is True
    assert isinstance(second[0], PublicReplyTransportError)
    assert relay_calls == 1


class TestRegistryRegistration:
    """确保 InspectorExec 之后的 tool 列表里有 zendesk_public_reply。"""

    def test_registered(self) -> None:
        from zd_agent.tools.registry import create_tool_registry
        reg = create_tool_registry()
        # registry 内部存储 attr 可能不同；用 list_names() 或 _tools
        names = (
            reg.list_names()
            if hasattr(reg, "list_names")
            else list(reg._tools.keys())
        )
        assert "zendesk_public_reply" in names
