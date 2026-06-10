"""SqlQuery tool: execute read-only SQL on SaaS production DB."""
from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.integrations.db_gateway import query_db_gateway
from zd_agent.integrations.db_gateway_mcp import query_db_gateway_mcp
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


_DATABASE_KEYS = {"prod", "billing", "plugin"}
_FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
    "DROP", "TRUNCATE", "ALTER", "CREATE",
    "GRANT", "REVOKE", "COPY", "EXECUTE", "CALL",
    "VACUUM", "ANALYZE", "REINDEX", "SET", "LOCK", "SHOW",
]
_DOLLAR_QUOTE_START = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")
_MAX_ERROR_SQL_CHARS = 800


def _strip_comments_and_string_literals(sql: str) -> str:
    """Remove SQL comments and quoted string contents for safety scanning.

    This is not a full SQL parser. It only prevents false positives such as
    ``SELECT 'please update your app'`` while preserving real statement
    structure and keywords outside literals.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if ch == "-" and nxt == "-":
            i += 2
            while i < n and sql[i] not in "\r\n":
                i += 1
            out.append(" ")
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            out.append(" ")
            continue

        if ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            out.append(" '' ")
            continue

        if ch == '"':
            i += 1
            while i < n:
                if sql[i] == '"' and i + 1 < n and sql[i + 1] == '"':
                    i += 2
                    continue
                if sql[i] == '"':
                    i += 1
                    break
                i += 1
            out.append(' "" ')
            continue

        if ch == "$":
            match = _DOLLAR_QUOTE_START.match(sql, i)
            if match:
                delimiter = match.group(0)
                close_at = sql.find(delimiter, match.end())
                if close_at != -1:
                    i = close_at + len(delimiter)
                    out.append(" $$ ")
                    continue

        out.append(ch)
        i += 1
    return "".join(out)


def _validate_read_only_sql(sql: str) -> str | None:
    cleaned = _strip_comments_and_string_literals(sql)
    stripped = cleaned.strip().rstrip(";").strip()
    if not stripped:
        return "empty SQL"
    if ";" in stripped:
        return "SQL contains multiple statements; only one SELECT allowed"
    upper = cleaned.upper()
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            return f"forbidden keyword detected: {kw}"
    first_word = re.match(r"\s*(\w+)", cleaned)
    fw = first_word.group(1).upper() if first_word else ""
    if fw not in ("SELECT", "WITH"):
        return f"SQL must start with SELECT or WITH, got: {fw or '(empty)'}"
    return None


def _compact_sql_for_error(sql: str) -> str:
    compact = re.sub(r"\s+", " ", sql).strip()
    if len(compact) > _MAX_ERROR_SQL_CHARS:
        return compact[:_MAX_ERROR_SQL_CHARS] + " ... (SQL truncated)"
    return compact


def _format_gateway_error(*, result: Any, database: str, sql: str) -> str:
    code = result.code or "ERROR"
    elapsed = f"{result.elapsed_ms}ms" if result.elapsed_ms else "unknown"
    error = result.error or "(empty error from DB gateway)"
    return (
        f"DB gateway query failed [{code}] "
        f"database={database} elapsed={elapsed}: {error}\n"
        f"Failed SQL: {_compact_sql_for_error(sql)}"
    )


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
        if not isinstance(db_key, str):
            return ToolResult(success=False, error="database must be a string")

        raw_sql = args.get("sql")
        if not isinstance(raw_sql, str):
            return ToolResult(success=False, error="sql must be a string")
        sql = raw_sql.strip()

        if db_key not in _DATABASE_KEYS:
            return ToolResult(
                success=False,
                error=f"unknown database key: {db_key}. Must be one of {sorted(_DATABASE_KEYS)}",
            )

        safety_error = _validate_read_only_sql(sql)
        if safety_error:
            return ToolResult(success=False, error=f"SQL safety check failed: {safety_error}")

        cfg = get_config()
        ticket_id = str(session.ticket_id)
        if not cfg.db_gateway.url or not cfg.db_gateway.api_key:
            return ToolResult(
                success=False,
                error=(
                    "DB Gateway 未配置；请设置 DB_GATEWAY_URL 和 DB_GATEWAY_API_KEY。"
                    "Teleport、DB proxy 与数据库凭据已迁移到独立 db-gateway 服务。"
                ),
            )

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
        return self._gateway_result_to_tool_result(
            result=result,
            session=session,
            database=database,
            sql=sql,
        )

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

        return self._gateway_result_to_tool_result(
            result=result,
            session=session,
            database=database,
            sql=sql,
        )

    def _gateway_result_to_tool_result(
        self,
        *,
        result: Any,
        session: "AgentSession",
        database: str,
        sql: str,
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
                error=_format_gateway_error(result=result, database=database, sql=sql),
            )
        summary_lines = [f"Rows: {result.row_count} | elapsed: {result.elapsed_ms}ms"]
        if result.truncated:
            summary_lines[0] += " (truncated by DB gateway)"
        summary_lines.append("")
        summary_lines.append(result.csv)
        return ToolResult(output="\n".join(summary_lines))
