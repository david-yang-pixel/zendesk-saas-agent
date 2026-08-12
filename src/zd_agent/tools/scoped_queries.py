from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING
from uuid import UUID

from sqlglot import exp, parse, parse_one
from sqlglot.errors import ParseError

from zd_agent.integrations.signoz_mcp import run_signoz_sz
from zd_agent.tools.base import Tool, ToolResult
from zd_agent.tools.signoz_tools import _rows_to_text
from zd_agent.tools.sql_query import SqlQuery

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_METADATA_TABLES = frozenset({"columns", "tables"})
_SIGNOZ_TABLES = {
    "signoz_logs.distributed_logs_v2": (
        "attributes_string['tenant_id'] = '{tenant}' "
        "OR positionCaseInsensitive(body, '{tenant}') > 0"
    ),
    "signoz_traces.distributed_signoz_index_v3": (
        "attributes_string['tenant_id'] = '{tenant}'"
    ),
}
_START_SENTINEL = "7711111111111111111"
_END_SENTINEL = "7722222222222222222"
_MUTATING_EXPRESSION_TYPES = tuple(
    getattr(exp, name)
    for name in (
        "Alter",
        "Command",
        "Copy",
        "Create",
        "Delete",
        "Drop",
        "Grant",
        "Insert",
        "Into",
        "Lock",
        "Merge",
        "Revoke",
        "Transaction",
        "Update",
    )
)
_POSTGRES_UNSAFE_FUNCTIONS = frozenset(
    {
        "dblink",
        "dblink_connect",
        "lo_export",
        "lo_import",
        "nextval",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_cancel_backend",
        "pg_create_restore_point",
        "pg_logical_emit_message",
        "pg_read_binary_file",
        "pg_read_file",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_sleep",
        "pg_terminate_backend",
        "set_config",
    }
)
_CLICKHOUSE_UNSAFE_FUNCTIONS = frozenset(
    {
        "azureblobstorage",
        "cluster",
        "clusterallreplicas",
        "executable",
        "executablepool",
        "file",
        "gcs",
        "hdfs",
        "jdbc",
        "mysql",
        "odbc",
        "postgresql",
        "remote",
        "remotesecure",
        "s3",
        "s3cluster",
        "url",
    }
)


class ScopedQueryError(ValueError):
    pass


def _canonical_uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ScopedQueryError(f"{name} must be a UUID string")
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError) as error:
        raise ScopedQueryError(f"{name} must be a valid UUID") from error


def _read_expression(sql: str, dialect: str) -> exp.Expression:
    try:
        expressions = parse(sql, read=dialect)
    except ParseError as error:
        raise ScopedQueryError(f"invalid {dialect} SQL: {error}") from error
    if len(expressions) != 1 or expressions[0] is None:
        raise ScopedQueryError("query must contain exactly one statement")
    expression = expressions[0]
    if not isinstance(expression, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ScopedQueryError("query must be one read-only SELECT or WITH statement")
    if any(isinstance(node, _MUTATING_EXPRESSION_TYPES) for node in expression.walk()):
        raise ScopedQueryError("query contains a non-read-only SQL operation")
    return expression


def _function_names(expression: exp.Expression) -> set[str]:
    return {
        function.name.casefold()
        for function in expression.find_all(exp.Func)
        if function.name
    }


def _reject_unsafe_functions(
    expression: exp.Expression,
    *,
    forbidden: frozenset[str],
) -> None:
    used = _function_names(expression) & forbidden
    if used:
        raise ScopedQueryError(
            "query uses a function that is not safe for the read-only investigation "
            f"boundary: {', '.join(sorted(used))}",
        )


def _cte_names(expression: exp.Expression) -> set[str]:
    return {cte.alias_or_name.casefold() for cte in expression.find_all(exp.CTE)}


def _physical_tables(expression: exp.Expression) -> tuple[exp.Table, ...]:
    ctes = _cte_names(expression)
    return tuple(
        table
        for table in expression.find_all(exp.Table)
        if table.name.casefold() not in ctes
    )


def _owner_tables(owner: exp.Select | None) -> tuple[exp.Table, ...]:
    if owner is None:
        return ()
    return tuple(
        table
        for table in owner.find_all(exp.Table)
        if table.find_ancestor(exp.Select) is owner
    )


def _is_conjunctive_scope_predicate(
    node: exp.Expression,
    owner: exp.Select | None,
) -> bool:
    current = node.parent
    found_filter = False
    while current is not None and current is not owner:
        if isinstance(current, exp.Or):
            return False
        if isinstance(current, (exp.Where, exp.Join, exp.Having)):
            found_filter = True
        current = current.parent
    return found_filter and current is owner


def _tenant_equality(
    expression: exp.Expression,
    *,
    table: exp.Table,
    tenant: str,
) -> bool:
    alias = table.alias_or_name.casefold()
    owner = table.find_ancestor(exp.Select)
    for equality in expression.find_all(exp.EQ):
        column = equality.left if isinstance(equality.left, exp.Column) else None
        literal = equality.right if isinstance(equality.right, exp.Literal) else None
        if column is None or literal is None:
            column = equality.right if isinstance(equality.right, exp.Column) else None
            literal = equality.left if isinstance(equality.left, exp.Literal) else None
        if column is None or literal is None or not literal.is_string:
            continue
        if column.name.casefold() not in {"tenant_id", "workspace_id"}:
            continue
        unambiguous = not column.table and len(_owner_tables(owner)) == 1
        if _is_conjunctive_scope_predicate(equality, owner) and (
            column.table.casefold() == alias or unambiguous
        ):
            if literal.this.casefold() == tenant.casefold():
                return True
    return False


def _known_object_equality(
    expression: exp.Expression,
    *,
    table: exp.Table,
    known_ids: set[str],
) -> bool:
    if not known_ids:
        return False
    alias = table.alias_or_name.casefold()
    owner = table.find_ancestor(exp.Select)
    allowed = {value.casefold() for value in known_ids}
    for equality in expression.find_all(exp.EQ):
        column = equality.left if isinstance(equality.left, exp.Column) else None
        literal = equality.right if isinstance(equality.right, exp.Literal) else None
        if column is None or literal is None:
            column = equality.right if isinstance(equality.right, exp.Column) else None
            literal = equality.left if isinstance(equality.left, exp.Literal) else None
        if column is None or literal is None or not literal.is_string:
            continue
        if column.name.casefold() != "id" and not column.name.casefold().endswith(
            "_id"
        ):
            continue
        unambiguous = not column.table and len(_owner_tables(owner)) == 1
        if _is_conjunctive_scope_predicate(equality, owner) and (
            column.table.casefold() == alias or unambiguous
        ):
            if literal.this.casefold() in allowed:
                return True
    return False


def _known_object_filter(
    expression: exp.Expression,
    *,
    table: exp.Table,
    known_ids: set[str],
) -> bool:
    if _known_object_equality(expression, table=table, known_ids=known_ids):
        return True
    if not known_ids:
        return False
    alias = table.alias_or_name.casefold()
    owner = table.find_ancestor(exp.Select)
    allowed = {value.casefold() for value in known_ids}
    for condition in expression.find_all(exp.In):
        column = condition.this if isinstance(condition.this, exp.Column) else None
        literals = tuple(condition.expressions)
        if (
            column is None
            or (
                column.name.casefold() != "id"
                and not column.name.casefold().endswith("_id")
            )
            or not literals
            or not all(
                isinstance(literal, exp.Literal) and literal.is_string
                for literal in literals
            )
        ):
            continue
        unambiguous = not column.table and len(_owner_tables(owner)) == 1
        if _is_conjunctive_scope_predicate(condition, owner) and (
            column.table.casefold() == alias or unambiguous
        ):
            values = {
                literal.this.casefold()
                for literal in literals
                if isinstance(literal, exp.Literal)
            }
            if values.issubset(allowed):
                return True
    return False


def _join_scope_edges(expression: exp.Expression) -> tuple[tuple[str, str], ...]:
    edges: list[tuple[str, str]] = []
    for equality in expression.find_all(exp.EQ):
        left = equality.left
        right = equality.right
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue
        owner = left.find_ancestor(exp.Select)
        if (
            equality.find_ancestor(exp.Select) is not owner
            or right.find_ancestor(exp.Select) is not owner
            or not _is_conjunctive_scope_predicate(equality, owner)
        ):
            continue
        if not left.table or not right.table:
            continue
        names = {left.name.casefold(), right.name.casefold()}
        relational = "id" in names and any(name.endswith("_id") for name in names)
        if relational:
            edges.append((left.table.casefold(), right.table.casefold()))
    return tuple(edges)


def _scoped_table_aliases(
    expression: exp.Expression,
    *,
    tables: tuple[exp.Table, ...],
    tenant: str,
    known_ids: set[str],
) -> set[str]:
    scoped = {
        table.alias_or_name.casefold()
        for table in tables
        if _tenant_equality(expression, table=table, tenant=tenant)
        or _known_object_filter(expression, table=table, known_ids=known_ids)
    }
    edges = _join_scope_edges(expression)
    changed = True
    while changed:
        changed = False
        for left, right in edges:
            if left in scoped and right not in scoped:
                scoped.add(right)
                changed = True
            elif right in scoped and left not in scoped:
                scoped.add(left)
                changed = True
    return scoped


def _validate_known_ids(sql: str, *, tenant: str, known_ids: set[str]) -> None:
    allowed = {tenant.casefold(), *(value.casefold() for value in known_ids)}
    unknown = sorted({match.casefold() for match in _UUID_RE.findall(sql)} - allowed)
    if unknown:
        raise ScopedQueryError(
            "query contains an object UUID that was not discovered from this "
            "ticket's tenant-scoped evidence",
        )


def scope_postgres_sql(
    sql: str,
    *,
    tenant: str,
    known_ids: set[str],
) -> str:
    expression = _read_expression(sql, "postgres")
    _reject_unsafe_functions(expression, forbidden=_POSTGRES_UNSAFE_FUNCTIONS)
    _validate_known_ids(sql, tenant=tenant, known_ids=known_ids)
    physical_tables = _physical_tables(expression)
    data_tables = tuple(
        table
        for table in physical_tables
        if not (
            table.db.casefold() == "information_schema"
            and table.name.casefold() in _METADATA_TABLES
        )
    )
    scoped_aliases = _scoped_table_aliases(
        expression,
        tables=data_tables,
        tenant=tenant,
        known_ids=known_ids,
    )
    for table in physical_tables:
        schema = table.db.casefold()
        if schema == "information_schema" and table.name.casefold() in _METADATA_TABLES:
            continue
        if not table.alias:
            raise ScopedQueryError(
                f"tenant data table {table.sql()} must use an explicit alias",
            )
        if table.alias_or_name.casefold() not in scoped_aliases:
            raise ScopedQueryError(
                f"tenant data table alias {table.alias_or_name!r} requires either a "
                "direct tenant_id/workspace_id equality to the resolved tenant or "
                "an id/*_id equality to an object discovered by this case",
            )
    return expression.sql(dialect="postgres")


def _replace_signoz_table(
    table: exp.Table,
    *,
    tenant: str,
    known_ids: set[str],
) -> None:
    full_name = ".".join(
        value for value in (table.catalog, table.db, table.name) if value
    ).casefold()
    predicate = _SIGNOZ_TABLES.get(full_name)
    if predicate is None:
        raise ScopedQueryError(f"raw telemetry table is not allowed: {full_name}")
    source = table.copy()
    source.set("alias", None)
    object_predicate = " OR ".join(
        f"positionCaseInsensitive(body, '{value}') > 0" for value in sorted(known_ids)
    )
    complete_predicate = predicate.format(tenant=tenant)
    if object_predicate and full_name.startswith("signoz_logs."):
        complete_predicate = f"{complete_predicate} OR {object_predicate}"
    scoped = parse_one(
        "SELECT * FROM "
        f"{source.sql(dialect='clickhouse')} WHERE timestamp >= {_START_SENTINEL} "
        f"AND timestamp < {_END_SENTINEL} AND ("
        f"{complete_predicate})",
        read="clickhouse",
    )
    table.replace(
        exp.Subquery(
            this=scoped,
            alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name)),
        ),
    )


def scope_signoz_sql(sql: str, *, tenant: str, known_ids: set[str]) -> str:
    prepared = sql.replace("{start_ns}", _START_SENTINEL).replace(
        "{end_ns}", _END_SENTINEL
    )
    expression = _read_expression(prepared, "clickhouse")
    _reject_unsafe_functions(expression, forbidden=_CLICKHOUSE_UNSAFE_FUNCTIONS)
    _validate_known_ids(prepared, tenant=tenant, known_ids=known_ids)
    tables = _physical_tables(expression)
    if not tables:
        raise ScopedQueryError(
            "telemetry query must read an allowed logs or traces table"
        )
    for table in tables:
        _replace_signoz_table(table, tenant=tenant, known_ids=known_ids)
    return (
        expression.sql(dialect="clickhouse")
        .replace(_START_SENTINEL, "{start_ns}")
        .replace(_END_SENTINEL, "{end_ns}")
    )


def remember_investigation_ids(session: AgentSession, output: str) -> None:
    session.investigation_scope_ids.update(
        match.casefold() for match in _UUID_RE.findall(output)
    )


class TenantScopedSqlQuery(SqlQuery):
    casework_scope_enforced = True
    description = (
        "Run an arbitrary read-only SELECT/WITH for deep Dify support investigation. "
        "Code binds the resolved tenant and rejects every tenant-data table alias "
        "without a direct tenant_id/workspace_id equality. Every table must use an "
        "explicit alias. Additional UUIDs must first be discovered from trusted "
        "tenant-scoped results. information_schema.columns/tables are available."
    )
    parameters = {
        "type": "object",
        "properties": {
            "database": {"type": "string", "enum": ["prod", "billing", "plugin"]},
            "tenant_id": {"type": "string", "description": "Resolved tenant UUID"},
            "sql": {"type": "string", "description": "One read-only SELECT/WITH"},
        },
        "required": ["database", "tenant_id", "sql"],
    }

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        try:
            tenant = _canonical_uuid(args.get("tenant_id"), "tenant_id")
            raw_sql = args.get("sql")
            if not isinstance(raw_sql, str):
                raise ScopedQueryError("sql must be a string")
            scoped = scope_postgres_sql(
                raw_sql,
                tenant=tenant,
                known_ids=session.investigation_scope_ids,
            )
        except ScopedQueryError as error:
            return ToolResult(success=False, error=str(error))
        result = await super().execute(
            {"database": args.get("database"), "sql": scoped},
            session,
        )
        if result.success:
            remember_investigation_ids(session, result.output)
        result.metadata.update({"tenant_scope": tenant, "scope_enforced": True})
        return result


class TenantScopedSignozQuery(Tool):
    name = "signoz_query"
    casework_scope_enforced = True
    description = (
        "Run arbitrary read-only ClickHouse SELECT/WITH over SigNoz logs or traces. "
        "Code rewrites every source table into a resolved-tenant and bounded-time "
        "subquery. Supports {start_ns}/{end_ns}. Use exact IDs discovered by this case."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tenant": {"type": "string", "description": "Resolved tenant UUID"},
            "sql": {"type": "string", "description": "ClickHouse SELECT/WITH"},
            "hours": {"type": "number", "minimum": 0.1, "maximum": 168},
        },
        "required": ["tenant", "sql"],
    }
    group = "log_query"
    concurrency_safe = True
    max_output_tokens = 8000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        try:
            tenant = _canonical_uuid(args.get("tenant"), "tenant")
            raw_sql = args.get("sql")
            if not isinstance(raw_sql, str):
                raise ScopedQueryError("sql must be a string")
            hours = float(args.get("hours", 1))
            if not 0.1 <= hours <= 168:
                raise ScopedQueryError("hours must be between 0.1 and 168")
            scoped = scope_signoz_sql(
                raw_sql,
                tenant=tenant,
                known_ids=session.investigation_scope_ids,
            )
        except (TypeError, ValueError, ScopedQueryError) as error:
            return ToolResult(success=False, error=str(error))
        rows = await run_signoz_sz("query", scoped, "--hours", str(hours))
        if rows is None:
            return ToolResult(success=False, error="SigNoz MCP query failed")
        output = _rows_to_text(rows)
        remember_investigation_ids(session, output)
        return ToolResult(
            output=output,
            metadata={"tenant_scope": tenant, "scope_enforced": True},
        )
