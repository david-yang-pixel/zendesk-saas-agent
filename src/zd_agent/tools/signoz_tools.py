"""SigNoz 日志查询工具组，经 vendored sz CLI（替换已失效的 SigNoz MCP）。

sz 直接打 SigNoz v4 query_range API，绕过 signoz-mcp-server 对 EE 后端的不兼容。
四个工具对应 sz 子命令：errors / traceback / services / query(raw SQL 兜底)。
"""
from __future__ import annotations

import json
from typing import Any

from zd_agent.integrations.sz_cli import run_sz
from zd_agent.tools.base import Tool, ToolResult

_GROUP = "platform_issue"
_MAX_OUT = 8000
_MIN_HOURS = 0.01
_MAX_HOURS = 168.0
_MIN_LIMIT = 1
_MAX_LIMIT = 1000


def _coerce_hours(args: dict[str, Any], default: float) -> float | ToolResult:
    try:
        hours = float(args.get("hours", default))
    except (TypeError, ValueError):
        return ToolResult(
            success=False,
            error=f"hours must be a number in [{_MIN_HOURS}, {_MAX_HOURS}]",
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


class SignozErrors(Tool):
    name = "signoz_errors"
    description = (
        "查 cloud.dify.ai 生产 ERROR/FATAL 日志（经 sz CLI）。可按 tenant_id、"
        "body 子串(grep)、k8s deployment 过滤。env 默认 production。"
        "workflow 异步任务跑在 deployment=dify-app-worker，不是 dify-api-oltp。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tenant": {"type": "string", "description": "tenant_id 精确过滤"},
            "grep": {"type": "string", "description": "body 子串过滤"},
            "deployment": {"type": "string", "description": "k8s.deployment.name，如 dify-api-oltp / dify-app-worker"},
            "env": {"type": "string", "description": "deployment.environment，默认 production"},
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

        sz_args = ["errors", "--env", str(args.get("env") or "production")]
        if args.get("tenant"):
            sz_args += ["--tenant", str(args["tenant"])]
        if args.get("grep"):
            sz_args += ["--grep", str(args["grep"])]
        if args.get("deployment"):
            sz_args += ["--deployment", str(args["deployment"])]
        sz_args += ["--hours", str(hours)]
        sz_args += ["--limit", str(limit)]
        rows = await run_sz(*sz_args)
        if rows is None:
            return ToolResult(success=False, error="sz errors 查询失败（SigNoz 不可达或 sz 缺失）")
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

        sz_args = ["traceback"]
        if args.get("id"):
            sz_args += ["--id", str(args["id"])]
        else:
            sz_args += ["--env", str(args.get("env") or "production")]
            if args.get("tenant"):
                sz_args += ["--tenant", str(args["tenant"])]
            if args.get("grep"):
                sz_args += ["--grep", str(args["grep"])]
        sz_args += ["--hours", str(hours)]
        sz_args += ["--limit", str(limit)]
        rows = await run_sz(*sz_args)
        if rows is None:
            return ToolResult(success=False, error="sz traceback 查询失败")
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

        sz_args = ["services", "--env", str(args.get("env") or "production"),
                   "--hours", str(hours), "--limit", str(limit)]
        rows = await run_sz(*sz_args)
        if rows is None:
            return ToolResult(success=False, error="sz services 查询失败")
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
            "sql": {"type": "string", "description": "ClickHouse SELECT，可含 {start_ns}/{end_ns}"},
            "hours": {"type": "number", "description": "{start_ns}/{end_ns} 的窗口小时，默认 1"},
        },
        "required": ["sql"],
    }
    group = _GROUP
    concurrency_safe = True
    max_output_tokens = _MAX_OUT

    async def execute(self, args: dict[str, Any], session: Any) -> ToolResult:
        sql = args.get("sql")
        if not sql:
            return ToolResult(success=False, error="sql 参数必填")
        hours = _coerce_hours(args, 1)
        if isinstance(hours, ToolResult):
            return hours
        rows = await run_sz("query", str(sql), "--hours", str(hours))
        if rows is None:
            return ToolResult(success=False, error="sz query 执行失败（SQL 错误或 SigNoz 不可达）")
        return ToolResult(output=_rows_to_text(rows))
