"""SigNoz telemetry tools backed by the remote SigNoz MCP.

The structured tools are the default investigation path. Four legacy sz
convenience tools remain available for focused errors, traceback, service, and
read-only fallback queries.
"""

from __future__ import annotations

import json
import math
from typing import Any

from zd_agent.integrations.signoz_mcp import call_signoz_tool, run_signoz_sz
from zd_agent.tools.base import Tool, ToolResult

_GROUP = "platform_issue"
_MAX_OUT = 8000
_MIN_HOURS = 0.01
_MAX_HOURS = 168.0
_MIN_LIMIT = 1
_MAX_LIMIT = 1000


_AGGREGATIONS = [
    "count",
    "count_distinct",
    "avg",
    "min",
    "max",
    "sum",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
]

SIGNOZ_STRUCTURED_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "signoz_list_services",
        "description": (
            "列出活跃 SigNoz services 和 Kubernetes deployments；"
            "不确定服务名时先调用，避免猜 deployment。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timeRange": {
                    "type": "string",
                    "description": "相对时间窗，如 30m、2h、1d；默认 1h",
                },
                "environment": {
                    "type": "string",
                    "description": "可选 deployment.environment",
                },
                "limit": {"type": "integer", "description": "1-1000；默认 30"},
            },
        },
    },
    {
        "name": "signoz_get_trace_details",
        "description": (
            "按精确 trace ID 读取完整 span 链，包含父子关系、服务、operation、"
            "耗时、状态、HTTP 字段和 attributes。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "traceId": {"type": "string", "description": "32 位十六进制 trace ID"},
                "timeRange": {"type": "string", "description": "相对时间窗；默认 6h"},
                "limit": {"type": "integer", "description": "1-1000；默认 500"},
            },
            "required": ["traceId"],
        },
    },
    {
        "name": "signoz_list_metrics",
        "description": "发现可用 SigNoz metric 名和元数据；查询容量或资源指标前先用它找真实名称。",
        "parameters": {
            "type": "object",
            "properties": {
                "searchText": {"type": "string", "description": "metric 名子串"},
                "timeRange": {"type": "string", "description": "默认 1h"},
                "limit": {"type": "integer", "description": "1-1000；默认 50"},
            },
        },
    },
    {
        "name": "signoz_get_field_keys",
        "description": "从有界近期样本发现 logs、traces 或 metrics 的 intrinsic/attribute/resource 字段。",
        "parameters": {
            "type": "object",
            "properties": {
                "signal": {"type": "string", "enum": ["logs", "traces", "metrics"]},
                "searchText": {"type": "string"},
                "timeRange": {"type": "string", "description": "默认 1h"},
                "limit": {"type": "integer", "description": "默认 100"},
            },
            "required": ["signal"],
        },
    },
    {
        "name": "signoz_get_field_values",
        "description": "列出一个安全日志、trace 或 metric 字段的高频值，用于确认真实字段取值。",
        "parameters": {
            "type": "object",
            "properties": {
                "signal": {"type": "string", "enum": ["logs", "traces", "metrics"]},
                "name": {"type": "string"},
                "fieldContext": {
                    "type": "string",
                    "enum": ["attribute", "resource", "intrinsic"],
                },
                "searchText": {"type": "string"},
                "timeRange": {"type": "string", "description": "默认 1h"},
                "limit": {"type": "integer", "description": "默认 50"},
            },
            "required": ["signal", "name", "fieldContext"],
        },
    },
    {
        "name": "signoz_search_logs",
        "description": "按环境、deployment、tenant、severity、trace ID 和正文搜索近期日志。",
        "parameters": {
            "type": "object",
            "properties": {
                "timeRange": {"type": "string", "description": "默认 1h"},
                "environment": {"type": "string"},
                "deployment": {"type": "string"},
                "tenant": {"type": "string"},
                "severity": {"type": "string"},
                "traceId": {"type": "string"},
                "searchText": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 100"},
                "offset": {"type": "integer", "description": "默认 0"},
            },
        },
    },
    {
        "name": "signoz_aggregate_logs",
        "description": "对近期日志做安全计数、分组、分位数或时间序列聚合；用于先确认影响范围再看样本。",
        "parameters": {
            "type": "object",
            "properties": {
                "aggregation": {"type": "string", "enum": _AGGREGATIONS},
                "aggregateOn": {"type": "string"},
                "groupBy": {
                    "type": "string",
                    "description": "逗号分隔安全字段，最多 3 个",
                },
                "requestType": {"type": "string", "enum": ["scalar", "time_series"]},
                "stepInterval": {"type": "integer", "description": "秒"},
                "timeRange": {"type": "string", "description": "默认 1h"},
                "environment": {"type": "string"},
                "deployment": {"type": "string"},
                "tenant": {"type": "string"},
                "severity": {"type": "string"},
                "searchText": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 100"},
            },
        },
    },
    {
        "name": "signoz_search_traces",
        "description": "按服务、operation、tenant、错误状态、耗时或精确 trace ID 搜索近期 spans。",
        "parameters": {
            "type": "object",
            "properties": {
                "timeRange": {"type": "string", "description": "默认 1h"},
                "service": {"type": "string"},
                "operation": {"type": "string"},
                "tenant": {"type": "string"},
                "environment": {"type": "string"},
                "deployment": {"type": "string"},
                "traceId": {"type": "string"},
                "error": {"type": "boolean"},
                "minDuration": {"type": "integer", "description": "纳秒"},
                "maxDuration": {"type": "integer", "description": "纳秒"},
                "limit": {"type": "integer", "description": "默认 100"},
                "offset": {"type": "integer", "description": "默认 0"},
            },
        },
    },
    {
        "name": "signoz_aggregate_traces",
        "description": "按延迟、错误、服务、operation、tenant 等安全字段聚合 spans，可输出时间序列。",
        "parameters": {
            "type": "object",
            "properties": {
                "aggregation": {"type": "string", "enum": _AGGREGATIONS},
                "aggregateOn": {"type": "string"},
                "groupBy": {
                    "type": "string",
                    "description": "逗号分隔安全字段，最多 3 个",
                },
                "requestType": {"type": "string", "enum": ["scalar", "time_series"]},
                "stepInterval": {"type": "integer", "description": "秒"},
                "timeRange": {"type": "string", "description": "默认 1h"},
                "service": {"type": "string"},
                "operation": {"type": "string"},
                "tenant": {"type": "string"},
                "environment": {"type": "string"},
                "deployment": {"type": "string"},
                "error": {"type": "boolean"},
                "minDuration": {"type": "integer"},
                "maxDuration": {"type": "integer"},
                "limit": {"type": "integer", "description": "默认 100"},
            },
        },
    },
    {
        "name": "signoz_query_metrics",
        "description": "按安全聚合和 Kubernetes 过滤查询 metric；metric 名未知时先用 signoz_list_metrics。",
        "parameters": {
            "type": "object",
            "properties": {
                "metricName": {"type": "string"},
                "aggregation": {
                    "type": "string",
                    "enum": ["avg", "min", "max", "sum", "latest"],
                },
                "groupBy": {
                    "type": "string",
                    "description": "逗号分隔安全字段，最多 3 个",
                },
                "requestType": {"type": "string", "enum": ["scalar", "time_series"]},
                "stepInterval": {"type": "integer", "description": "秒"},
                "timeRange": {"type": "string", "description": "默认 1h"},
                "environment": {"type": "string"},
                "deployment": {"type": "string"},
                "pod": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 100"},
            },
            "required": ["metricName"],
        },
    },
)


def _coerce_hours(args: dict[str, Any], default: float) -> float | ToolResult:
    try:
        hours = float(args.get("hours", default))
    except (TypeError, ValueError):
        return ToolResult(
            success=False,
            error=f"hours must be a number in [{_MIN_HOURS}, {_MAX_HOURS}]",
        )
    if not math.isfinite(hours):
        return ToolResult(
            success=False,
            error=f"hours must be a finite number in [{_MIN_HOURS}, {_MAX_HOURS}]",
        )
    return max(_MIN_HOURS, min(hours, _MAX_HOURS))


def _coerce_limit(args: dict[str, Any], default: int) -> int | ToolResult:
    try:
        limit = int(args.get("limit", default))
    except (TypeError, ValueError):
        return ToolResult(
            success=False,
            error=f"limit must be an integer in [{_MIN_LIMIT}, {_MAX_LIMIT}]",
        )
    return max(_MIN_LIMIT, min(limit, _MAX_LIMIT))


def _coerce_optional_string(
    args: dict[str, Any],
    name: str,
    *,
    default: str | None = None,
) -> str | ToolResult | None:
    value = args.get(name)
    if value is None:
        return default
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    cleaned = value.strip()
    return cleaned or default


def _coerce_required_string(args: dict[str, Any], name: str) -> str | ToolResult:
    value = args.get(name)
    if not value:
        return ToolResult(success=False, error=f"{name} 参数必填")
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        return ToolResult(success=False, error=f"{name} 参数必填")
    return cleaned


def _rows_to_text(rows: Any) -> str:
    """sz --json 返回的 list[dict] → 紧凑文本。空 → 提示语。"""
    if not rows:
        return "(无匹配行)"
    if not isinstance(rows, list):
        return json.dumps(rows, ensure_ascii=False)[:4000]
    lines = []
    for r in rows:
        if isinstance(r, dict):
            lines.append(" | ".join(f"{k}={v}" for k, v in r.items()))
        else:
            lines.append(str(r))
    return "\n".join(lines)


class SignozStructuredTool(Tool):
    """Thin agent-facing wrapper around one validated remote SigNoz tool."""

    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    def __init__(self, spec: dict[str, Any]) -> None:
        self.name = str(spec["name"])
        self.description = str(spec["description"])
        self.parameters = dict(spec["parameters"])

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        payload = await call_signoz_tool(self.name, args)
        if not payload.get("success"):
            code = payload.get("code") or "UNKNOWN"
            error = payload.get("error") or "unknown error"
            return ToolResult(
                success=False,
                error=f"SigNoz MCP failed [{code}]: {error}",
                metadata={"mcp_code": str(code)},
            )

        output = payload.get("output")
        if isinstance(output, str) and output.strip():
            return ToolResult(output=output)
        data = payload.get("data")
        if data is None:
            return ToolResult(output="(查询成功，无返回数据)")
        return ToolResult(
            output=json.dumps(data, ensure_ascii=False, indent=2, default=str),
        )


def structured_signoz_tools() -> list[SignozStructuredTool]:
    return [SignozStructuredTool(spec) for spec in SIGNOZ_STRUCTURED_SPECS]


class SignozErrors(Tool):
    name = "signoz_errors"
    description = (
        "查 cloud.dify.ai 生产 ERROR/FATAL 日志（经远程 sz MCP）。可按 tenant_id、"
        "body 子串(grep)、k8s deployment 过滤。env 默认 production。"
        "workflow 异步任务跑在 deployment=dify-app-worker，不是 dify-api-oltp。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tenant": {"type": "string", "description": "tenant_id 精确过滤"},
            "grep": {"type": "string", "description": "body 子串过滤"},
            "deployment": {
                "type": "string",
                "description": "k8s.deployment.name，如 dify-api-oltp / dify-app-worker",
            },
            "env": {
                "type": "string",
                "description": "deployment.environment，默认 production",
            },
            "hours": {"type": "number", "description": "lookback 小时，默认 1"},
            "limit": {"type": "integer", "description": "行上限，默认 30"},
        },
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        hours = _coerce_hours(args, 1)
        if isinstance(hours, ToolResult):
            return hours
        limit = _coerce_limit(args, 30)
        if isinstance(limit, ToolResult):
            return limit

        env = _coerce_optional_string(args, "env", default="production")
        tenant = _coerce_optional_string(args, "tenant")
        grep = _coerce_optional_string(args, "grep")
        deployment = _coerce_optional_string(args, "deployment")
        for value in (env, tenant, grep, deployment):
            if isinstance(value, ToolResult):
                return value

        sz_args = ["errors", "--env", env or "production"]
        if tenant:
            sz_args += ["--tenant", tenant]
        if grep:
            sz_args += ["--grep", grep]
        if deployment:
            sz_args += ["--deployment", deployment]
        sz_args += ["--hours", str(hours)]
        sz_args += ["--limit", str(limit)]
        rows = await run_signoz_sz(*sz_args)
        if rows is None or (isinstance(rows, dict) and rows.get("success") is False):
            return ToolResult(
                success=False,
                error="远程 sz MCP errors 查询失败（SigNoz MCP 不可达或查询错误）",
            )
        return ToolResult(output=_rows_to_text(rows))


class SignozTraceback(Tool):
    name = "signoz_traceback"
    description = (
        "拉完整 Python stacktrace（exception.stacktrace 字段），按 log id / "
        "tenant_id / body 子串定位。要看完整堆栈用这个，不要用 signoz_errors（body 只一行）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "精确 log id（最准）"},
            "tenant": {"type": "string"},
            "grep": {"type": "string"},
            "env": {"type": "string", "description": "默认 production"},
            "hours": {"type": "number", "description": "默认 24"},
            "limit": {"type": "integer", "description": "默认 5"},
        },
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        hours = _coerce_hours(args, 24)
        if isinstance(hours, ToolResult):
            return hours
        limit = _coerce_limit(args, 5)
        if isinstance(limit, ToolResult):
            return limit

        log_id = _coerce_optional_string(args, "id")
        env = _coerce_optional_string(args, "env", default="production")
        tenant = _coerce_optional_string(args, "tenant")
        grep = _coerce_optional_string(args, "grep")
        for value in (log_id, env, tenant, grep):
            if isinstance(value, ToolResult):
                return value

        sz_args = ["traceback"]
        if log_id:
            sz_args += ["--id", log_id]
        else:
            sz_args += ["--env", env or "production"]
            if tenant:
                sz_args += ["--tenant", tenant]
            if grep:
                sz_args += ["--grep", grep]
        sz_args += ["--hours", str(hours)]
        sz_args += ["--limit", str(limit)]
        rows = await run_signoz_sz(*sz_args)
        if rows is None or (isinstance(rows, dict) and rows.get("success") is False):
            return ToolResult(success=False, error="远程 sz MCP traceback 查询失败")
        return ToolResult(output=_rows_to_text(rows))


class SignozServices(Tool):
    name = "signoz_services"
    description = "列 cloud.dify.ai 生产当前活跃 service / k8s deployment（查不到日志时用来核对正确的 deployment 名）。"
    parameters = {
        "type": "object",
        "properties": {
            "env": {"type": "string", "description": "默认 production"},
            "hours": {"type": "number", "description": "默认 1"},
        },
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        hours = _coerce_hours(args, 1)
        if isinstance(hours, ToolResult):
            return hours
        limit = _coerce_limit(args, 30)
        if isinstance(limit, ToolResult):
            return limit

        env = _coerce_optional_string(args, "env", default="production")
        if isinstance(env, ToolResult):
            return env

        sz_args = [
            "services",
            "--env",
            env or "production",
            "--hours",
            str(hours),
            "--limit",
            str(limit),
        ]
        rows = await run_signoz_sz(*sz_args)
        if rows is None or (isinstance(rows, dict) and rows.get("success") is False):
            return ToolResult(success=False, error="远程 sz MCP services 查询失败")
        return ToolResult(output=_rows_to_text(rows))


class SignozQuery(Tool):
    name = "signoz_query"
    description = (
        "对 SigNoz ClickHouse 跑任意 SELECT（兜底，前三个工具搞不定时用）。"
        "日志表 signoz_logs.distributed_logs_v2；resource 字段用 resources_string['k8s.deployment.name'] 等；"
        "log 属性用 attributes_string['tenant_id']；时间用 {start_ns}/{end_ns} 占位符（UInt64 纳秒）。"
        "结构 DESCRIBE 用 query 跑 'DESCRIBE signoz_logs.distributed_logs_v2'。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "ClickHouse SELECT，可含 {start_ns}/{end_ns}",
            },
            "hours": {
                "type": "number",
                "description": "{start_ns}/{end_ns} 的窗口小时，默认 1",
            },
        },
        "required": ["sql"],
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        sql = _coerce_required_string(args, "sql")
        if isinstance(sql, ToolResult):
            return sql
        hours = _coerce_hours(args, 1)
        if isinstance(hours, ToolResult):
            return hours
        rows = await run_signoz_sz("query", sql, "--hours", str(hours))
        if rows is None or (isinstance(rows, dict) and rows.get("success") is False):
            return ToolResult(
                success=False,
                error="远程 sz MCP query 执行失败（SQL 错误或 SigNoz MCP 不可达）",
            )
        return ToolResult(output=_rows_to_text(rows))
