from __future__ import annotations

from typing import Any, TYPE_CHECKING
from uuid import UUID

from zd_agent.config import get_config
from zd_agent.integrations.db_gateway_mcp import query_db_gateway_mcp
from zd_agent.tools.base import Tool, ToolResult
from zd_agent.tools.scoped_queries import remember_investigation_ids

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


def _uuid(value: object, name: str, *, required: bool = False) -> str:
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID string")
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{name} must be a valid UUID") from error


def _query(*, tenant_id: str, dataset_id: str, document_id: str, days: int) -> str:
    filters = [f"d.tenant_id = '{tenant_id}'"]
    if dataset_id:
        filters.append(f"d.dataset_id = '{dataset_id}'")
    if document_id:
        filters.append(f"d.id = '{document_id}'")
    if not dataset_id and not document_id:
        filters.append(
            f"(d.created_at >= now() - interval '{days} days' "
            "OR d.indexing_status <> 'completed' OR d.is_paused IS TRUE)"
        )
    where = " AND ".join(filters)
    return f"""
WITH segment_counts AS (
  SELECT document_id, count(*) AS segment_count,
         count(*) FILTER (WHERE status = 'completed') AS completed_segments,
         count(*) FILTER (WHERE status = 'error') AS error_segments
  FROM document_segments
  WHERE tenant_id = '{tenant_id}'
  GROUP BY document_id
)
SELECT now() AS queried_at, d.id AS document_id, d.dataset_id,
       ds.name AS dataset_name, d.name AS document_name,
       d.indexing_status, d.is_paused, d.error, d.created_at,
       d.processing_started_at, d.parsing_completed_at,
       d.cleaning_completed_at, d.splitting_completed_at,
       d.completed_at, d.paused_at, d.stopped_at, d.updated_at,
       COALESCE(s.segment_count, 0) AS segment_count,
       COALESCE(s.completed_segments, 0) AS completed_segments,
       COALESCE(s.error_segments, 0) AS error_segments
FROM documents d
JOIN datasets ds ON ds.id = d.dataset_id
LEFT JOIN segment_counts s ON s.document_id = d.id
WHERE {where}
ORDER BY d.created_at DESC
LIMIT 100
""".strip()


class DocumentIndexingStatus(Tool):
    name = "document_indexing_status"
    description = (
        "Read current document-indexing state for the code-resolved Dify workspace "
        "through a fixed tenant-scoped DB Gateway query. Use first for stuck, paused, "
        "waiting, queued, or repeatedly uploaded knowledge documents. It returns "
        "document/dataset IDs, lifecycle timestamps, pause/error state, and segment "
        "counts. It does not prove queue acceptance or worker receipt; use returned "
        "IDs for tenant-scoped log investigation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string", "description": "Resolved tenant UUID"},
            "dataset_id": {"type": "string", "description": "Optional dataset UUID"},
            "document_id": {"type": "string", "description": "Optional document UUID"},
            "recent_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 90,
                "description": "Recent window when no exact object ID is known",
            },
        },
        "required": ["tenant_id"],
    }
    group = "database_query"
    concurrency_safe = True
    max_output_tokens = 8000

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        try:
            tenant_id = _uuid(args.get("tenant_id"), "tenant_id", required=True)
            dataset_id = _uuid(args.get("dataset_id"), "dataset_id")
            document_id = _uuid(args.get("document_id"), "document_id")
            days = int(args.get("recent_days", 30))
            if not 1 <= days <= 90:
                raise ValueError("recent_days must be between 1 and 90")
        except (TypeError, ValueError) as error:
            return ToolResult(success=False, error=str(error))
        cfg = get_config()
        if not cfg.db_gateway.url or not cfg.db_gateway.api_key:
            return ToolResult(success=False, error="DB Gateway is not configured")
        result = await query_db_gateway_mcp(
            cfg=cfg,
            database="prod",
            sql=_query(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                days=days,
            ),
            ticket_id=str(session.ticket_id),
        )
        if not result.success:
            return ToolResult(
                success=False,
                error=f"DB Gateway query failed [{result.code}]: {result.error or ''}",
            )
        header = f"Rows: {result.row_count} | elapsed: {result.elapsed_ms}ms"
        if result.truncated:
            header += " (truncated)"
        output = f"{header}\n\n{result.csv}"
        remember_investigation_ids(session, output)
        return ToolResult(output=output)
