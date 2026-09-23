"""Export SaaS Agent runs, rounds, and recorded content through the log API.

Dry-run is the default. Uncertain POSTs are held for manual reconciliation,
because the manager's existing create endpoints have no idempotency keys.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx

TERMINAL_STATUSES = ("completed", "failed", "cancelled", "awaiting_customer", "human_takeover")
SAFE_TOOL_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,99}\Z")

PENDING_SQL = """
SELECT j.job_id, j.ticket_id, j.status, j.created_at,
       j.finished_at AS job_finished_at, e.event_type,
       r.started_at, r.finished_at AS run_finished_at,
       r.active_question ->> 'model' AS model,
       r.active_question::text AS active_question_json,
       r.outcome::text AS outcome_json,
       r.tool_call_records::text AS tool_call_records_json,
       x.remote_run_id, COALESCE(x.rounds_reported, 0) AS rounds_reported,
       x.round_remote_ids::text AS round_remote_ids_json,
       x.tool_calls_reported::text AS tool_calls_reported_json,
       x.pending_operation,
       GREATEST(j.updated_at, COALESCE(r.updated_at, j.updated_at)) AS source_updated_at
FROM case_jobs j
JOIN events e ON e.event_id = j.event_id
LEFT JOIN run_records r ON r.job_id = j.job_id
LEFT JOIN run_log_exports x ON x.job_id = j.job_id
WHERE j.status = ANY($1::text[])
  AND (x.source_updated_at IS NULL OR x.source_updated_at <
       GREATEST(j.updated_at, COALESCE(r.updated_at, j.updated_at)))
ORDER BY j.created_at, j.job_id
LIMIT $2
"""

CLAIM_CREATE_SQL = """
INSERT INTO run_log_exports (job_id, pending_operation) VALUES ($1, 'create')
ON CONFLICT (job_id) DO UPDATE SET pending_operation = 'create'
WHERE run_log_exports.remote_run_id IS NULL AND run_log_exports.pending_operation IS NULL
"""
SAVE_RUN_SQL = """
UPDATE run_log_exports SET remote_run_id = $2, pending_operation = NULL, synced_at = NOW()
WHERE job_id = $1 AND pending_operation = 'create'
"""
CLAIM_ROUND_SQL = """
UPDATE run_log_exports SET pending_operation = $2
WHERE job_id = $1 AND pending_operation IS NULL AND remote_run_id = $3
"""
SAVE_ROUND_SQL = """
UPDATE run_log_exports SET rounds_reported = $2,
    round_remote_ids = jsonb_set(round_remote_ids, ARRAY[($2::int)::text], to_jsonb($5::int), true),
    pending_operation = NULL, synced_at = NOW()
WHERE job_id = $1 AND remote_run_id = $3 AND pending_operation = $4
"""
SAVE_TOOL_SQL = """
UPDATE run_log_exports SET
    tool_calls_reported = jsonb_set(tool_calls_reported, ARRAY[($2::int)::text], to_jsonb($3::int), true),
    pending_operation = NULL, synced_at = NOW()
WHERE job_id = $1 AND remote_run_id = $4 AND pending_operation = $5
"""
CLEAR_PENDING_SQL = """
UPDATE run_log_exports SET pending_operation = NULL
WHERE job_id = $1 AND pending_operation = $2
"""
MARK_EXPORTED_SQL = """
UPDATE run_log_exports SET source_updated_at = $2, synced_at = NOW()
WHERE job_id = $1 AND remote_run_id = $3
"""


@dataclass(frozen=True)
class ExportCredentials:
    base_url: str
    api_key: str


def load_credentials(path: Path) -> ExportCredentials:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Log export credentials must be a JSON object")
    base_url = raw.get("base_url")
    api_key = raw.get("api_key")
    if not isinstance(base_url, str) or not isinstance(api_key, str) or not api_key:
        raise ValueError("Log export URL and API key are required")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Log export URL must be an HTTPS origin without credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Log export URL must be an origin, not an API path")
    return ExportCredentials(base_url=base_url.rstrip("/"), api_key=api_key)


def _database_dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL must use postgresql+asyncpg")
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _bounded_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 2_147_483_647 else None


def _duration_ms(value: object, started: datetime | None, finished: datetime | None) -> int | None:
    if value is not None:
        try:
            parsed = int(float(str(value)))
        except (ValueError, OverflowError):
            pass
        else:
            if 0 <= parsed <= 2_147_483_647:
                return parsed
    if started is not None and finished is not None:
        return _bounded_int(int((finished - started).total_seconds() * 1000))
    return None


def _outcome(row: Any) -> dict[str, Any]:
    raw = row["outcome_json"]
    if raw is None:
        return {}
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


def _stored_object(raw: object) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


def _tool_details(row: Any) -> dict[str, dict[str, Any]]:
    raw = row["tool_call_records_json"]
    value = json.loads(raw) if isinstance(raw, str) else raw
    if value is None:
        return {}
    if not isinstance(value, list):
        raise ValueError("Invalid stored tool call records")
    details: dict[str, dict[str, Any]] = {}
    for step in value:
        if not isinstance(step, dict) or step.get("type") != "tool_call":
            continue
        call_id = step.get("toolCallId")
        if isinstance(call_id, str) and call_id:
            details[call_id] = step
    return details


def _result_text(value: object) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _safe_rounds(outcome: dict[str, Any]) -> list[dict[str, object]] | None:
    raw = outcome.get("rounds")
    if raw is None:
        return None  # Historical run: never invent its round count or usage.
    if not isinstance(raw, list) or len(raw) > 500:
        raise ValueError("Invalid stored round telemetry")
    result: list[dict[str, object]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict) or item.get("round") != index:
            raise ValueError("Invalid stored round order")
        model = item.get("model")
        reason = item.get("stopReason")
        if model is not None and (not isinstance(model, str) or len(model) > 100):
            raise ValueError("Invalid stored round model")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 50):
            raise ValueError("Invalid stored round stop reason")
        assistant_content = item.get("assistantContent")
        if assistant_content is not None and not isinstance(assistant_content, str):
            raise ValueError("Invalid stored assistant content")
        count = _bounded_int(item.get("toolCallCount"))
        if count is None:
            raise ValueError("Invalid stored tool count")
        metrics: dict[str, object] = {
            "round": index, "model": model, "input_tokens": None,
            "output_tokens": None, "duration_ms": None,
            "assistant_content": assistant_content, "stop_reason": reason,
            "has_tool_calls": count > 0, "tool_call_count": count,
        }
        for source, target in (
            ("inputTokens", "input_tokens"),
            ("outputTokens", "output_tokens"),
            ("durationMs", "duration_ms"),
        ):
            value = item.get(source)
            if value is not None:
                bounded = _bounded_int(value)
                if bounded is None:
                    raise ValueError("Invalid stored round metric")
                metrics[target] = bounded
        result.append(metrics)
    return result


def _safe_tool_calls(
    outcome: dict[str, Any], details: dict[str, dict[str, Any]] | None = None,
) -> list[list[dict[str, object]]]:
    """Join terminal tool telemetry to the recorded call by its Pi tool-call ID."""
    details = details or {}
    raw = outcome.get("rounds")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 500:
        raise ValueError("Invalid stored round telemetry")
    result: list[list[dict[str, object]]] = []
    for round_item in raw:
        if not isinstance(round_item, dict):
            raise ValueError("Invalid stored round telemetry")
        raw_calls = round_item.get("toolCalls", [])
        if not isinstance(raw_calls, list) or len(raw_calls) > 100:
            raise ValueError("Invalid stored tool telemetry")
        calls: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for item in raw_calls:
            if not isinstance(item, dict):
                raise ValueError("Invalid stored tool telemetry")
            call_id = item.get("toolCallId")
            name = item.get("toolName")
            status = item.get("status")
            if (not isinstance(call_id, str) or not call_id or len(call_id) > 255
                    or call_id in seen_ids or not isinstance(name, str)
                    or SAFE_TOOL_NAME.fullmatch(name) is None
                    or not isinstance(status, str)
                    or status not in {"unknown", "running", "succeeded", "failed"}):
                raise ValueError("Invalid stored tool telemetry")
            seen_ids.add(call_id)
            duration = item.get("durationMs")
            if duration is not None and _bounded_int(duration) is None:
                raise ValueError("Invalid stored tool duration")
            if status not in {"succeeded", "failed"}:
                continue
            content = details.get(call_id, {})
            arguments = content.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                arguments = {"value": arguments}
            calls.append({
                "tool_name": name, "arguments": arguments,
                "result_preview": _result_text(content.get("result")),
                "success": status == "succeeded", "error_message": None,
                "duration_ms": duration,
            })
        result.append(calls)
    return result


def _checkpoint_map(value: object, *, positive: bool) -> dict[int, int]:
    raw = json.loads(value) if isinstance(value, str) else value
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Invalid log export checkpoint")
    result: dict[int, int] = {}
    for key, item in raw.items():
        if (not isinstance(key, str) or not key.isdecimal() or len(key) > 3
                or not 1 <= int(key) <= 500
                or _bounded_int(item) is None or (positive and item == 0)):
            raise ValueError("Invalid log export checkpoint")
        result[int(key)] = item
    return result


def _token_totals(rounds: list[dict[str, object]] | None) -> tuple[int | None, int | None]:
    if rounds is None:
        return None, None

    def total(field: str) -> int | None:
        values = [item[field] for item in rounds if item[field] is not None]
        return _bounded_int(sum(values)) if values else None  # type: ignore[arg-type]

    return total("input_tokens"), total("output_tokens")


def to_create_payload(row: Any) -> dict[str, object]:
    model = row["model"]
    if not isinstance(model, str) or not model or len(model) > 100:
        model = None
    active_question = _stored_object(row["active_question_json"])
    prompt = active_question.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ValueError("Invalid stored agent prompt")
    return {
        "ticket_id": row["ticket_id"], "source": "saas",
        # Existing manager schema limits session_id to 32 characters.
        "session_id": hashlib.sha256(row["job_id"].encode()).hexdigest()[:32],
        "trigger_type": "mention" if row["event_type"] == "ticket.mention" else "webhook",
        "model": model,
        "started_at": (row["started_at"] or row["created_at"]).isoformat(),
        "task_preview": prompt,
    }


def to_finish_payload(row: Any, outcome: dict[str, Any], rounds: list[dict[str, object]] | None) -> dict[str, object]:
    started = row["started_at"]
    finished = row["run_finished_at"] or row["job_finished_at"]
    input_tokens, output_tokens = _token_totals(rounds)
    return {
        "status": row["status"],
        "finished_at": finished.isoformat() if finished else None,
        "duration_ms": _duration_ms(outcome.get("total_duration_ms"), started, finished),
        "total_rounds": len(rounds) if rounds is not None else None,
        "total_input_tokens": input_tokens, "total_output_tokens": output_tokens,
        "error_message": None,
    }


async def pending_rows(connection: asyncpg.Connection, limit: int) -> list[Any]:
    return await connection.fetch(PENDING_SQL, list(TERMINAL_STATUSES), limit)


async def preview(connection: asyncpg.Connection) -> None:
    rows = await connection.fetch(PENDING_SQL, list(TERMINAL_STATUSES), 100_000)
    statuses = Counter(row["status"] for row in rows)
    print(json.dumps({"mode": "dry-run", "pending": len(rows),
                      "manual_review": sum(row["pending_operation"] is not None for row in rows),
                      "statuses": dict(statuses)}, sort_keys=True))


def _acknowledged_id(response: httpx.Response) -> int:
    response.raise_for_status()
    payload = response.json()
    result = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(result, int) or isinstance(result, bool) or result <= 0:
        raise ValueError("Log API returned an invalid acknowledgement")
    return result


async def _claim(connection: asyncpg.Connection, sql: str, *args: object) -> None:
    result = await connection.execute(sql, *args)
    if not result.endswith(" 1"):
        raise ValueError("Log export checkpoint is already claimed")


async def _post_once(
    connection: asyncpg.Connection,
    client: httpx.AsyncClient,
    *, url: str, headers: dict[str, str], payload: dict[str, object],
    job_id: str, operation: str,
) -> int:
    try:
        return _acknowledged_id(await client.post(url, headers=headers, json=payload))
    except httpx.HTTPStatusError as exc:
        # These rejection responses establish that the create did not occur.
        # Timeouts, 5xx, and missing acknowledgements remain ambiguous.
        if exc.response.status_code in {400, 401, 403, 404, 422}:
            await connection.execute(CLEAR_PENDING_SQL, job_id, operation)
        raise


async def export_row(
    connection: asyncpg.Connection,
    client: httpx.AsyncClient,
    credentials: ExportCredentials,
    row: Any,
) -> None:
    base = f"{credentials.base_url}/api/agent/logs/runs"
    headers = {"X-Agent-Key": credentials.api_key}
    outcome = _outcome(row)
    rounds = _safe_rounds(outcome)
    tools_by_round = _safe_tool_calls(outcome, _tool_details(row))
    round_ids = _checkpoint_map(row["round_remote_ids_json"], positive=True)
    tool_counts = _checkpoint_map(row["tool_calls_reported_json"], positive=False)
    if row["pending_operation"] is not None:
        raise ValueError("Log export has an uncertain POST requiring manual review")
    remote_id = row["remote_run_id"]
    if remote_id is None:
        await _claim(connection, CLAIM_CREATE_SQL, row["job_id"])
        remote_id = await _post_once(
            connection, client, url=base, headers=headers,
            payload=to_create_payload(row), job_id=row["job_id"], operation="create",
        )
        await _claim(connection, SAVE_RUN_SQL, row["job_id"], remote_id)
    if row["rounds_reported"] > len(rounds or []):
        raise ValueError("Stored round count is behind export checkpoint")
    for index, round_payload in enumerate(rounds or [], 1):
        round_id = round_ids.get(index)
        if index > row["rounds_reported"]:
            operation = f"round:{index}"
            await _claim(connection, CLAIM_ROUND_SQL, row["job_id"], operation, remote_id)
            round_id = await _post_once(
                connection, client, url=f"{base}/{remote_id}/rounds", headers=headers,
                payload=round_payload, job_id=row["job_id"], operation=operation,
            )
            await _claim(connection, SAVE_ROUND_SQL, row["job_id"], index, remote_id, operation, round_id)
            round_ids[index] = round_id
        calls = tools_by_round[index - 1]
        if tool_counts.get(index, 0) > len(calls):
            raise ValueError("Stored tool count is behind export checkpoint")
        if calls and round_id is None:
            raise ValueError("Remote round ID is missing for tool export")
        for tool_index, tool_payload in enumerate(calls, 1):
            if tool_index <= tool_counts.get(index, 0):
                continue
            operation = f"tool:{index}:{tool_index}"
            await _claim(connection, CLAIM_ROUND_SQL, row["job_id"], operation, remote_id)
            await _post_once(
                connection, client,
                url=f"{base}/{remote_id}/rounds/{round_id}/tool-calls",
                headers=headers, payload=tool_payload,
                job_id=row["job_id"], operation=operation,
            )
            await _claim(connection, SAVE_TOOL_SQL, row["job_id"], index, tool_index, remote_id, operation)
            tool_counts[index] = tool_index
    response = await client.put(
        f"{base}/{remote_id}", headers=headers, json=to_finish_payload(row, outcome, rounds)
    )
    _acknowledged_id(response)
    await connection.execute(MARK_EXPORTED_SQL, row["job_id"], row["source_updated_at"], remote_id)


async def export_batch(
    connection: asyncpg.Connection,
    client: httpx.AsyncClient,
    credentials: ExportCredentials,
    *, limit: int,
) -> tuple[int, int]:
    rows = await pending_rows(connection, limit)
    succeeded = 0
    failed = 0
    for row in rows:
        try:
            await export_row(connection, client, credentials, row)
            succeeded += 1
        except httpx.HTTPStatusError as exc:
            print(f"Log export rejected: HTTP {exc.response.status_code}")
            failed += 1
        except (httpx.RequestError, ValueError, asyncpg.PostgresError) as exc:
            print(f"Log export failed: {type(exc).__name__}")
            failed += 1
    return succeeded, failed


async def export_pending(
    connection: asyncpg.Connection, credentials: ExportCredentials, *, limit: int,
) -> tuple[int, int]:
    total_succeeded = 0
    total_failed = 0
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        while True:
            succeeded, failed = await export_batch(connection, client, credentials, limit=limit)
            total_succeeded += succeeded
            total_failed += failed
            if succeeded + failed < limit or succeeded == 0 or failed > 0:
                break
    return total_succeeded, total_failed


async def run(mode: str, *, credentials_file: Path | None, interval: int, limit: int) -> None:
    credentials = load_credentials(credentials_file) if mode != "dry-run" and credentials_file else None
    if mode != "dry-run" and credentials is None:
        raise ValueError("--credentials-file is required for exports")
    while True:
        try:
            connection = await asyncpg.connect(_database_dsn(), timeout=10)
            try:
                if mode == "dry-run":
                    await preview(connection)
                    return
                assert credentials is not None
                succeeded, failed = await export_pending(connection, credentials, limit=limit)
                print(f"Log export cycle: succeeded={succeeded} failed={failed}")
                if mode == "once":
                    if failed:
                        raise RuntimeError("Some log exports failed")
                    return
            finally:
                await connection.close()
        except (OSError, asyncpg.PostgresError, httpx.RequestError) as exc:
            if mode != "watch":
                raise
            print(f"Log export cycle unavailable: {type(exc).__name__}")
        if mode != "watch":
            return
        await asyncio.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "once", "watch"), default="dry-run")
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.interval < 10 or not 1 <= args.limit <= 1000:
        parser.error("--interval must be >=10 and --limit must be 1..1000")
    asyncio.run(run(args.mode, credentials_file=args.credentials_file, interval=args.interval, limit=args.limit))


if __name__ == "__main__":
    main()
