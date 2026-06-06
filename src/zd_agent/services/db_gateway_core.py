"""Core read-only DB gateway execution logic."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zd_agent.integrations.postgres import (
    PsqlSafetyError,
    TeleportAuthExpired,
    audit_log,
    run_psql,
    validate_read_only_sql,
)


_DB_KEY_TO_ATTR = {
    "prod": "db_prod",
    "billing": "db_billing",
    "plugin": "db_plugin",
}

_DB_KEY_TO_PORT_ATTR = {
    "prod": "port_prod",
    "billing": "port_billing",
    "plugin": "port_plugin",
}


@dataclass
class GatewayQueryResult:
    success: bool
    csv: str = ""
    row_count: int = 0
    elapsed_ms: int = 0
    truncated: bool = False
    code: str = ""
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "csv": self.csv,
            "row_count": self.row_count,
            "elapsed_ms": self.elapsed_ms,
            "truncated": self.truncated,
            "code": self.code,
            "error": self.error,
        }


def _audit_path(cfg: Any) -> Path:
    return (
        Path(cfg.resources.base_dir)
        / cfg.resources.state_dir
        / "logs"
        / "db_gateway_audit.log"
    )


async def execute_gateway_query(
    *,
    cfg: Any,
    database: str,
    sql: str,
    ticket_id: str = "",
) -> GatewayQueryResult:
    """Validate and execute one read-only query through local DB proxy."""
    sql = (sql or "").strip()
    if database not in _DB_KEY_TO_ATTR:
        return GatewayQueryResult(
            success=False,
            code="UNKNOWN_DATABASE",
            error=f"unknown database key: {database}. Must be one of {list(_DB_KEY_TO_ATTR)}",
        )

    try:
        validate_read_only_sql(sql)
    except PsqlSafetyError as e:
        return GatewayQueryResult(
            success=False,
            code="SQL_SAFETY_ERROR",
            error=f"SQL safety check failed: {e}",
        )

    db_name = getattr(cfg.postgres, _DB_KEY_TO_ATTR[database])
    db_port = getattr(
        cfg.postgres,
        _DB_KEY_TO_PORT_ATTR[database],
        cfg.postgres.port,
    )

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
            log_path=_audit_path(cfg),
            database=database,
            sql=sql,
            status="TELEPORT_EXPIRED",
            ticket_id=ticket_id,
            error=str(e),
        )
        return GatewayQueryResult(
            success=False,
            code="TELEPORT_EXPIRED",
            error=str(e),
        )
    except Exception as e:
        audit_log(
            log_path=_audit_path(cfg),
            database=database,
            sql=sql,
            status="ERROR",
            ticket_id=ticket_id,
            error=str(e),
        )
        return GatewayQueryResult(
            success=False,
            code="QUERY_ERROR",
            error=str(e),
        )

    audit_log(
        log_path=_audit_path(cfg),
        database=database,
        sql=sql,
        status="OK",
        ticket_id=ticket_id,
        row_count=result.row_count,
        elapsed_ms=result.elapsed_ms,
    )
    return GatewayQueryResult(
        success=True,
        csv=result.csv,
        row_count=result.row_count,
        elapsed_ms=result.elapsed_ms,
        truncated=result.truncated,
    )
