"""Tests for runtime.customer_profile (P2-8 minimal).

覆盖：
- 正常路径：拉到 requester + 历史工单 ID 列表
- 排除当前工单 ID
- 上限 _MAX_RECENT_TICKETS
- 任一步骤失败/超时 → 返回空 dict（不抛异常）
- system prompt 段：有 profile 时渲染，无 profile 时不渲染
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from zd_agent.runtime.customer_profile import (
    _MAX_RECENT_TICKETS,
    prefetch_customer_profile,
)


def _make_mcp_call(
    *,
    ticket_resp: dict | None = None,
    user_resp: dict | None = None,
    search_resp: dict | None = None,
    raise_on_search: bool = False,
    raise_on_ticket: bool = False,
):
    async def fake_call(name: str, arguments: dict):
        if name == "get_ticket":
            if raise_on_ticket:
                return {"success": False, "code": "ZENDESK_API_ERROR", "error": "network"}
            return {"success": True, "data": ticket_resp}
        if name == "get_user":
            return {"success": True, "data": user_resp}
        if name == "search_tickets":
            if raise_on_search:
                return {"success": False, "code": "ZENDESK_API_ERROR", "error": "zd 500"}
            return {"success": True, "data": search_resp or {"results": []}}
        raise AssertionError(f"unexpected Zendesk MCP tool: {name}")

    return fake_call


class TestPrefetchHappyPath:
    @pytest.mark.asyncio
    async def test_returns_email_and_recent_tickets(self) -> None:
        mcp_call = _make_mcp_call(
            ticket_resp={"id": 9999, "requester_id": 12345},
            user_resp={"id": 12345, "email": "alice@example.com"},
            search_resp={"results": [
                {"id": 9999, "subject": "current ticket"},  # 应排除
                {"id": 2001, "subject": "old 1"},
                {"id": 2002, "subject": "old 2"},
            ]},
        )
        with patch(
            "zd_agent.runtime.customer_profile.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            profile = await prefetch_customer_profile(9999)

        assert profile["requester_email"] == "alice@example.com"
        # 当前工单 9999 应被排除
        assert 9999 not in profile["recent_ticket_ids"]
        assert profile["recent_ticket_ids"] == [2001, 2002]
        assert profile["recent_ticket_count"] == 2

    @pytest.mark.asyncio
    async def test_truncates_to_max(self) -> None:
        many = [{"id": 3000 + i} for i in range(20)]
        mcp_call = _make_mcp_call(
            ticket_resp={"id": 9999, "requester_id": 12345},
            user_resp={"id": 12345, "email": "x@y.com"},
            search_resp={"results": many},
        )
        with patch(
            "zd_agent.runtime.customer_profile.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            profile = await prefetch_customer_profile(9999)
        assert len(profile["recent_ticket_ids"]) == _MAX_RECENT_TICKETS


class TestPrefetchFailures:
    @pytest.mark.asyncio
    async def test_get_ticket_returns_none_empty_profile(self) -> None:
        mcp_call = _make_mcp_call(ticket_resp=None)
        with patch(
            "zd_agent.runtime.customer_profile.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            profile = await prefetch_customer_profile(9999)
        assert profile == {}

    @pytest.mark.asyncio
    async def test_no_requester_id_empty_profile(self) -> None:
        mcp_call = _make_mcp_call(ticket_resp={"id": 9999})  # no requester_id
        with patch(
            "zd_agent.runtime.customer_profile.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            profile = await prefetch_customer_profile(9999)
        assert profile == {}

    @pytest.mark.asyncio
    async def test_no_email_empty_profile(self) -> None:
        mcp_call = _make_mcp_call(
            ticket_resp={"id": 9999, "requester_id": 12345},
            user_resp={"id": 12345, "email": ""},  # 空 email
        )
        with patch(
            "zd_agent.runtime.customer_profile.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            profile = await prefetch_customer_profile(9999)
        assert profile == {}

    @pytest.mark.asyncio
    async def test_search_failure_returns_empty_recent_list(self) -> None:
        """search 失败 → 仍返回 email + 空 recent list（不影响主流程）"""
        mcp_call = _make_mcp_call(
            ticket_resp={"id": 9999, "requester_id": 12345},
            user_resp={"id": 12345, "email": "x@y.com"},
            raise_on_search=True,
        )
        with patch(
            "zd_agent.runtime.customer_profile.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            profile = await prefetch_customer_profile(9999)
        # email 拿到了，但 recent_ids 空
        assert profile["requester_email"] == "x@y.com"
        assert profile["recent_ticket_ids"] == []
        assert profile["recent_ticket_count"] == 0

    @pytest.mark.asyncio
    async def test_get_ticket_raises_returns_empty(self) -> None:
        """任何顶层异常都被吞掉，返回 {}"""
        mcp_call = _make_mcp_call(raise_on_ticket=True)
        with patch(
            "zd_agent.runtime.customer_profile.call_zendesk_tool",
            side_effect=mcp_call,
        ):
            profile = await prefetch_customer_profile(9999)
        assert profile == {}


class TestPrefetchTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self) -> None:
        """to_thread 跑超时（slow Zendesk） → 返回 {}"""
        async def slow_call(name: str, arguments: dict):
            await asyncio.sleep(10)
            return {"success": True, "data": {"id": 9999, "requester_id": 1}}

        with patch(
            "zd_agent.runtime.customer_profile.call_zendesk_tool",
            side_effect=slow_call,
        ):
            # 用更短的 timeout 测试（patch _PROFILE_TIMEOUT_SECONDS = 0.3）
            with patch(
                "zd_agent.runtime.customer_profile._PROFILE_TIMEOUT_SECONDS", 0.3,
            ):
                profile = await prefetch_customer_profile(9999)

        assert profile == {}


class TestPromptInjection:
    """customer_profile 注入到 system prompt 的渲染逻辑"""

    def test_no_profile_section_not_rendered(self) -> None:
        from zd_agent.runtime.prompt_loader import build_system_prompt
        p = build_system_prompt()
        assert "客户画像" not in p

    def test_empty_profile_section_not_rendered(self) -> None:
        from zd_agent.runtime.prompt_loader import build_system_prompt
        p = build_system_prompt(customer_profile={})
        assert "客户画像" not in p

    def test_new_customer_says_new(self) -> None:
        from zd_agent.runtime.prompt_loader import build_system_prompt
        p = build_system_prompt(customer_profile={
            "requester_email": "new@x.com",
            "recent_ticket_ids": [],
            "recent_ticket_count": 0,
        })
        assert "客户画像" in p
        assert "新客户" in p
        assert "new@x.com" in p

    def test_repeat_customer_lists_ticket_ids(self) -> None:
        from zd_agent.runtime.prompt_loader import build_system_prompt
        p = build_system_prompt(customer_profile={
            "requester_email": "old@x.com",
            "recent_ticket_ids": [2509, 2510, 2486],
            "recent_ticket_count": 3,
        })
        assert "客户画像" in p
        assert "#2509" in p
        assert "#2510" in p
        assert "#2486" in p
        # 应有 ticket_record_read 提示
        assert "ticket_record_read" in p or "zendesk_get_ticket" in p
        assert "不要重复 ask" in p
