"""Tests for observability.metrics (M8 埋点函数).

每个 emit_* 应该：
- 调用结构化 logger.info 一次
- 携带正确的 event name（``metric.*``）
- 携带正确的字段（ticket_id 字符串化 / customer_phrase 截断 / round 数值）
- 不抛 exception（即使输入异常）
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from zd_agent.observability.metrics import (
    emit_abandoned,
    emit_agent_public_reply_sent,
    emit_customer_reopen,
    emit_customer_requested_human,
)


@pytest.fixture
def mock_logger():
    with patch("zd_agent.observability.metrics.logger") as m:
        yield m


class TestCustomerRequestedHuman:
    def test_basic_emit(self, mock_logger) -> None:
        emit_customer_requested_human(
            ticket_id=12345,
            source="zendesk_webhook",
            customer_phrase="I want to talk to a human",
            ask_count_so_far=2,
        )
        mock_logger.info.assert_called_once()
        args, kwargs = mock_logger.info.call_args
        assert args[0] == "metric.customer_requested_human"
        assert kwargs["ticket_id"] == "12345"
        assert kwargs["source"] == "zendesk_webhook"
        assert kwargs["customer_phrase"] == "I want to talk to a human"
        assert kwargs["ask_count_so_far"] == 2

    def test_customer_phrase_truncated(self, mock_logger) -> None:
        """customer_phrase 超过 120 字符应被截断"""
        long_phrase = "a" * 500
        emit_customer_requested_human(
            ticket_id=1, customer_phrase=long_phrase,
        )
        kwargs = mock_logger.info.call_args.kwargs
        assert len(kwargs["customer_phrase"]) == 120

    def test_default_args_safe(self, mock_logger) -> None:
        """只传 ticket_id 也不该抛"""
        emit_customer_requested_human(ticket_id=99)
        mock_logger.info.assert_called_once()
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["source"] == "unknown"
        assert kwargs["customer_phrase"] == ""
        assert kwargs["ask_count_so_far"] == 0


class TestAgentPublicReplySent:
    def test_basic_emit(self, mock_logger) -> None:
        emit_agent_public_reply_sent(
            ticket_id=12345,
            reply_type="ask",
            template_name="C",
            body_chars=200,
        )
        mock_logger.info.assert_called_once_with(
            "metric.agent_public_reply_sent",
            ticket_id="12345",
            reply_type="ask",
            template_name="C",
            body_chars=200,
        )

    @pytest.mark.parametrize("reply_type", [
        "ask", "solution", "handoff", "closed",
    ])
    def test_all_reply_types(self, mock_logger, reply_type) -> None:
        emit_agent_public_reply_sent(
            ticket_id=1, reply_type=reply_type, body_chars=50,
        )
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["reply_type"] == reply_type


class TestCustomerReopen:
    def test_basic_emit(self, mock_logger) -> None:
        emit_customer_reopen(
            ticket_id=12345,
            prev_state="processed",
            source="zendesk_webhook",
            hours_since_processed=2.5,
        )
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["prev_state"] == "processed"
        assert kwargs["hours_since_processed"] == 2.5

    def test_hours_rounded_to_2_decimals(self, mock_logger) -> None:
        emit_customer_reopen(
            ticket_id=1, prev_state="processed", source="x",
            hours_since_processed=1.23456789,
        )
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["hours_since_processed"] == 1.23

    def test_none_hours_passed_through(self, mock_logger) -> None:
        emit_customer_reopen(
            ticket_id=1, prev_state="processed", source="x",
            hours_since_processed=None,
        )
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["hours_since_processed"] is None


class TestAbandoned:
    def test_basic_emit(self, mock_logger) -> None:
        emit_abandoned(
            ticket_id=12345,
            age_hours=24.5,
            last_verdict="greeting",
            ask_count=3,
        )
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["age_hours"] == 24.5
        assert kwargs["last_verdict"] == "greeting"
        assert kwargs["ask_count"] == 3

    def test_age_hours_rounded(self, mock_logger) -> None:
        emit_abandoned(ticket_id=1, age_hours=24.123456)
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["age_hours"] == 24.12
