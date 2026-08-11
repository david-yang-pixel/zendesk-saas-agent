"""Tests for v2 observability metrics.

每个 emit_* 应该：
- 调用结构化 logger.info 一次
- 携带正确的 event name（``metric.*``）
- 携带正确的字段
- 不抛 exception（即使输入异常）
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from zd_agent.observability.metrics import (
    emit_agent_public_reply_sent,
    emit_customer_reopen,
)


@pytest.fixture
def mock_logger():
    with patch("zd_agent.observability.metrics.logger") as m:
        yield m


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

    @pytest.mark.parametrize("reply_type", ["ask", "solution", "closed"])
    def test_all_reply_types(self, mock_logger, reply_type) -> None:
        emit_agent_public_reply_sent(
            ticket_id=1,
            reply_type=reply_type,
            body_chars=50,
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
            ticket_id=1,
            prev_state="processed",
            source="x",
            hours_since_processed=1.23456789,
        )
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["hours_since_processed"] == 1.23

    def test_none_hours_passed_through(self, mock_logger) -> None:
        emit_customer_reopen(
            ticket_id=1,
            prev_state="processed",
            source="x",
            hours_since_processed=None,
        )
        kwargs = mock_logger.info.call_args.kwargs
        assert kwargs["hours_since_processed"] is None
