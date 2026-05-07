"""SigNoz tools: 5 query tools for logs/traces/services/field metadata."""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.integrations.signoz import SignozClient
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


def _client() -> SignozClient:
    cfg = get_config().signoz
    if not cfg.url or not cfg.api_key:
        raise RuntimeError("SigNoz not configured (missing SIGNOZ_URL or SIGNOZ_API_KEY)")
    return SignozClient(url=cfg.url, api_key=cfg.api_key)


def _trunc_json(data: Any, limit: int = 15000) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)[:limit]


class SignozSearchLogs(Tool):
    name = "signoz_search_logs"
    description = (
        "按 service.name + severity + 关键词搜索 SigNoz 日志。severity 如 "
        "ERROR/WARN/INFO；query 会在 body 字段做 contains 匹配。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "severity": {"type": "string", "description": "ERROR/WARN/INFO"},
            "query": {"type": "string", "description": "body 包含的关键词"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "limit": {"type": "integer", "default": 100},
        },
        "required": ["service_name", "start", "end"],
    }
    group = "platform_issue"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            client = _client()
            result = await client.search_logs(
                service_name=args["service_name"],
                severity=args.get("severity"),
                query=args.get("query"),
                # /api/v3/query_range 期望 epoch ms 整数
                start=args["start"],
                end=args["end"],
                limit=args.get("limit", 100),
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(output=_trunc_json(result))


class SignozSearchTraces(Tool):
    name = "signoz_search_traces"
    description = "按 service.name 搜索 SigNoz 链路追踪（trace list）"
    parameters = {
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["service_name", "start", "end"],
    }
    group = "platform_issue"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            client = _client()
            result = await client.search_traces(
                service_name=args["service_name"],
                start=args["start"],
                end=args["end"],
                limit=args.get("limit", 50),
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(output=_trunc_json(result))


class SignozListServices(Tool):
    name = "signoz_list_services"
    description = "列出 SigNoz 里所有 service 名（用于确定 service_name 参数）"
    parameters = {
        "type": "object",
        "properties": {
            "start": {"type": "string"},
            "end": {"type": "string"},
        },
        "required": ["start", "end"],
    }
    group = "platform_issue"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            client = _client()
            # /api/v1/services（v1，老 endpoint）期待字符串 epoch ms
            result = await client.list_services(
                start=args["start"],
                end=args["end"],
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(output=_trunc_json(result, limit=5000))


class SignozGetFieldKeys(Tool):
    name = "signoz_get_field_keys"
    description = "获取 SigNoz 日志/追踪的可过滤字段 key 列表（用于发现可用字段）"
    parameters = {
        "type": "object",
        "properties": {
            "data_source": {"type": "string", "enum": ["logs", "traces"], "default": "logs"},
        },
        "required": [],
    }
    group = "platform_issue"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            client = _client()
            result = await client.get_field_keys(data_source=args.get("data_source", "logs"))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(output=_trunc_json(result, limit=10000))


class SignozGetFieldValues(Tool):
    name = "signoz_get_field_values"
    description = "获取指定字段的已知取值（用于精确过滤）"
    parameters = {
        "type": "object",
        "properties": {
            "data_source": {"type": "string", "enum": ["logs", "traces"], "default": "logs"},
            "field_key": {"type": "string"},
        },
        "required": ["field_key"],
    }
    group = "platform_issue"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            client = _client()
            result = await client.get_field_values(
                data_source=args.get("data_source", "logs"),
                field_key=args["field_key"],
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(output=_trunc_json(result, limit=5000))
