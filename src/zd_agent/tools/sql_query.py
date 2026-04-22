"""SqlQuery tool: execute read-only SQL on SaaS production DB via psql."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.integrations.postgres import (
    PsqlSafetyError,
    TeleportAuthExpired,
    audit_log,
    run_psql,
    validate_read_only_sql,
)
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


_DB_KEY_TO_ATTR = {
    "prod": "db_prod",
    "billing": "db_billing",
    "plugin": "db_plugin",
}


class SqlQuery(Tool):
    name = "sql_query"
    description = (
        "在 SaaS 生产只读库执行 SELECT 查询。严格只读：禁止 INSERT/UPDATE/DELETE/"
        "DROP/TRUNCATE/ALTER 等写操作。超时 30 秒，最多返回 1000 行。database 参数："
        "prod（langgenius 主库）/ billing（计费库）/ plugin（plugin daemon 库）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "enum": ["prod", "billing", "plugin"],
                "description": "目标数据库别名：prod=langgenius, billing=billing, plugin=dify_plugin_daemon",
            },
            "sql": {
                "type": "string",
                "description": "要执行的 SELECT 查询（单语句，无需尾部分号）",
            },
        },
        "required": ["database", "sql"],
    }
    group = "database_query"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        db_key = args.get("database")
        sql = (args.get("sql") or "").strip()

        if db_key not in _DB_KEY_TO_ATTR:
            return ToolResult(
                success=False,
                error=f"unknown database key: {db_key}. Must be one of {list(_DB_KEY_TO_ATTR)}",
            )

        # 1. Read-only validation
        try:
            validate_read_only_sql(sql)
        except PsqlSafetyError as e:
            return ToolResult(success=False, error=f"SQL safety check failed: {e}")

        cfg = get_config()
        db_name = getattr(cfg.postgres, _DB_KEY_TO_ATTR[db_key])
        audit_path = (
            Path(cfg.resources.base_dir)
            / cfg.resources.state_dir
            / "logs"
            / "sql_audit.log"
        )
        ticket_id = str(session.ticket_id)

        # 2. Execute
        try:
            result = await run_psql(
                host=cfg.postgres.host,
                port=cfg.postgres.port,
                user=cfg.postgres.user,
                password=cfg.postgres.password,
                database=db_name,
                sql=sql,
                timeout=cfg.postgres.query_timeout_seconds,
                max_rows=cfg.postgres.max_rows,
            )
        except TeleportAuthExpired as e:
            audit_log(
                log_path=audit_path, database=db_key, sql=sql,
                status="TELEPORT_EXPIRED", ticket_id=ticket_id, error=str(e),
            )
            session.mark_waiting_for_auth()
            return ToolResult(
                success=False,
                error="Teleport 认证过期或端口转发中断；本工单挂起，认证恢复后自动重试",
            )
        except Exception as e:
            audit_log(
                log_path=audit_path, database=db_key, sql=sql,
                status="ERROR", ticket_id=ticket_id, error=str(e),
            )
            return ToolResult(success=False, error=f"SQL execution failed: {e}")

        # 3. Audit + return
        audit_log(
            log_path=audit_path, database=db_key, sql=sql,
            status="OK", ticket_id=ticket_id,
            row_count=result.row_count, elapsed_ms=result.elapsed_ms,
        )

        summary_lines = [f"Rows: {result.row_count} | elapsed: {result.elapsed_ms}ms"]
        if result.truncated:
            summary_lines[0] += f" (truncated at {cfg.postgres.max_rows})"
        summary_lines.append("")
        summary_lines.append(result.csv)
        return ToolResult(output="\n".join(summary_lines))
