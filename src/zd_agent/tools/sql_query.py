"""SqlQuery tool: execute read-only SQL on SaaS production DB."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.integrations.db_gateway import query_db_gateway
from zd_agent.integrations.db_gateway_mcp import query_db_gateway_mcp
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

# 每个 database 对应一个独立的 Teleport proxy 端口（--db-name 在 proxy 启动时固定）
_DB_KEY_TO_PORT_ATTR = {
    "prod": "port_prod",
    "billing": "port_billing",
    "plugin": "port_plugin",
}


class SqlQuery(Tool):
    name = "sql_query"
    description = (
        "在 SaaS 生产只读库执行 SELECT 查询；配置 DB Gateway 时底层经 DB Gateway MCP 调用，"
        "不要自行连接数据库、不要启动 Teleport/DB proxy。严格只读：禁止 INSERT/UPDATE/DELETE/"
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
    # 限 8K tokens（约 32K chars）。SQL 返回大表会让 message_store 暴涨。
    # agent 要详细数据可以 LIMIT / WHERE narrow 再查。
    max_output_tokens = 8000

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
        ticket_id = str(session.ticket_id)
        if cfg.db_gateway.url:
            if getattr(cfg.db_gateway, "mcp_enabled", True):
                return await self._execute_via_gateway_mcp(
                    cfg=cfg,
                    database=db_key,
                    sql=sql,
                    ticket_id=ticket_id,
                    session=session,
                )
            return await self._execute_via_gateway(
                cfg=cfg,
                database=db_key,
                sql=sql,
                ticket_id=ticket_id,
                session=session,
            )

        db_name = getattr(cfg.postgres, _DB_KEY_TO_ATTR[db_key])
        # 每个 database 走独立 Teleport proxy 端口
        db_port = getattr(
            cfg.postgres, _DB_KEY_TO_PORT_ATTR[db_key], cfg.postgres.port,
        )
        audit_path = (
            Path(cfg.resources.base_dir)
            / cfg.resources.state_dir
            / "logs"
            / "sql_audit.log"
        )

        # 2. Execute
        try:
            result = await run_psql(
                host=cfg.postgres.host,
                port=db_port,
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

    async def _execute_via_gateway_mcp(
        self,
        *,
        cfg: Any,
        database: str,
        sql: str,
        ticket_id: str,
        session: "AgentSession",
    ) -> ToolResult:
        try:
            result = await query_db_gateway_mcp(
                cfg=cfg,
                database=database,
                sql=sql,
                ticket_id=ticket_id,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"DB gateway MCP request failed: {e}")
        return self._gateway_result_to_tool_result(result=result, session=session)

    async def _execute_via_gateway(
        self,
        *,
        cfg: Any,
        database: str,
        sql: str,
        ticket_id: str,
        session: "AgentSession",
    ) -> ToolResult:
        try:
            result = await query_db_gateway(
                url=cfg.db_gateway.url,
                api_key=cfg.db_gateway.api_key,
                database=database,
                sql=sql,
                ticket_id=ticket_id,
                timeout=cfg.db_gateway.request_timeout_seconds,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"DB gateway request failed: {e}")

        return self._gateway_result_to_tool_result(result=result, session=session)

    def _gateway_result_to_tool_result(
        self,
        *,
        result: Any,
        session: "AgentSession",
    ) -> ToolResult:
        if not result.success:
            if result.code == "TELEPORT_EXPIRED":
                session.mark_waiting_for_auth()
                return ToolResult(
                    success=False,
                    error="DB Gateway Teleport 认证过期或端口转发中断；本工单挂起，认证恢复后自动重试",
                )
            return ToolResult(
                success=False,
                error=f"DB gateway query failed [{result.code or 'ERROR'}]: {result.error}",
            )
        summary_lines = [f"Rows: {result.row_count} | elapsed: {result.elapsed_ms}ms"]
        if result.truncated:
            summary_lines[0] += " (truncated by DB gateway)"
        summary_lines.append("")
        summary_lines.append(result.csv)
        return ToolResult(output="\n".join(summary_lines))
