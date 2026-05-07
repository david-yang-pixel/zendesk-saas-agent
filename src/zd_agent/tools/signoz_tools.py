"""SigNoz tools: 5 query tools for logs/traces/services/field metadata."""
from __future__ import annotations

import json
from datetime import datetime, timezone
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


_NOW_OFFSET_RE = __import__("re").compile(
    r"^now(?:\s*-\s*(\d+)\s*([smhd]))?$",
    __import__("re").IGNORECASE,
)
_UNIT_TO_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _to_epoch_ms(value: str | int) -> int:
    """规范化 SigNoz 时间参数到 **epoch ms 整数**。

    支持的输入格式：
    - int（epoch s 或 epoch ms，自动判别）
    - 全数字字符串（同上）
    - ISO 8601: "2026-05-07T06:00:00+00:00" / "2026-05-07T06:00:00Z" / 无时区
    - "now" / "now-5m" / "now-1h" / "now-2d"（LLM 可能这么传）
    """
    if isinstance(value, int):
        # 区分秒和毫秒：> 1e12 ≈ 2001 年 9 月之后，按 ms；否则当 s
        return value if value > 10**12 else value * 1000

    s = str(value).strip()

    # 全数字 → 当 epoch
    if s.isdigit():
        return _to_epoch_ms(int(s))

    # "now" / "now-5m"
    m = _NOW_OFFSET_RE.match(s)
    if m:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        amt, unit = m.group(1), m.group(2)
        if amt and unit:
            return now_ms - int(amt) * _UNIT_TO_SECONDS[unit.lower()] * 1000
        return now_ms

    # ISO 8601
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        pass
    raise ValueError(
        f"signoz: 无法识别的时间格式 {value!r}；用 ISO 8601 / epoch ms / 'now-5m'"
    )


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
                start=_to_epoch_ms(args["start"]),
                end=_to_epoch_ms(args["end"]),
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
                start=_to_epoch_ms(args["start"]),
                end=_to_epoch_ms(args["end"]),
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
            # /api/v1/services 这边历史上也接受 string，但保险起见也用 int
            result = await client.list_services(
                start=_to_epoch_ms(args["start"]),
                end=_to_epoch_ms(args["end"]),
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
