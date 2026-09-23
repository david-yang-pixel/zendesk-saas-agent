from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from deploy import sync_run_logs


def _row(**overrides: object) -> dict[str, Any]:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    values: dict[str, Any] = {
        "job_id": "job-1", "ticket_id": 3726, "status": "completed",
        "created_at": now - timedelta(minutes=3), "job_finished_at": now,
        "event_type": "ticket.mention", "started_at": now - timedelta(minutes=2),
        "run_finished_at": now, "model": "gpt-5.6-terra",
        "active_question_json": json.dumps({"prompt": "Full initial prompt"}),
        "tool_call_records_json": "[]",
        "outcome_json": json.dumps({
            "total_duration_ms": 120000,
            "final_reply": "private customer reply",
            "rounds": [
                {"round": 1, "model": "gpt-5.6-terra", "inputTokens": 35,
                 "outputTokens": 4, "durationMs": 250, "stopReason": "toolUse",
                 "toolCallCount": 1, "cacheReadTokens": 20, "reasoningTokens": 2,
                 "private": "must not leave source"},
                {"round": 2, "model": "gpt-5.6-terra", "inputTokens": 40,
                 "outputTokens": 8, "durationMs": 500, "stopReason": "stop",
                 "toolCallCount": 0},
            ],
        }),
        "remote_run_id": None, "rounds_reported": 0,
        "round_remote_ids_json": "{}", "tool_calls_reported_json": "{}",
        "pending_operation": None,
        "source_updated_at": now,
    }
    values.update(overrides)
    return values


def test_payloads_include_initial_prompt_and_assistant_text() -> None:
    row = _row()
    outcome = sync_run_logs._outcome(row)  # pyright: ignore[reportPrivateUsage]
    outcome["rounds"][0]["assistantContent"] = "Working through the request"
    outcome["rounds"][0]["promptContent"] = '{"input":"Full model request"}'
    rounds = sync_run_logs._safe_rounds(outcome)  # pyright: ignore[reportPrivateUsage]
    create = sync_run_logs.to_create_payload(row)
    finish = sync_run_logs.to_finish_payload(row, outcome, rounds)

    assert create["source"] == "saas"
    assert create["trigger_type"] == "mention"
    assert len(str(create["session_id"])) == 32
    assert create["task_preview"] == "Full initial prompt"
    assert finish["total_rounds"] == 2
    assert finish["total_input_tokens"] == 75
    assert finish["total_output_tokens"] == 12
    assert rounds is not None
    assert rounds[0]["assistant_content"] == "Working through the request"
    assert rounds[0]["prompt_content"] == '{"input":"Full model request"}'
    assert rounds[0]["tool_call_count"] == 1
    encoded = json.dumps([create, rounds, finish])
    assert "private customer reply" not in encoded
    assert "must not leave source" not in encoded


def test_historical_run_does_not_fabricate_rounds_or_tokens() -> None:
    row = _row(
        started_at=None, run_finished_at=None,
        outcome_json=json.dumps({"final_reply": "private customer reply"}),
    )
    outcome = sync_run_logs._outcome(row)  # pyright: ignore[reportPrivateUsage]
    rounds = sync_run_logs._safe_rounds(outcome)  # pyright: ignore[reportPrivateUsage]
    finish = sync_run_logs.to_finish_payload(row, outcome, rounds)
    assert finish["total_rounds"] is None
    assert finish["total_input_tokens"] is None
    assert finish["duration_ms"] is None
    assert finish["finished_at"] is not None


def test_invalid_stored_rounds_fail_closed() -> None:
    with pytest.raises(ValueError, match="round order"):
        sync_run_logs._safe_rounds(  # pyright: ignore[reportPrivateUsage]
            {"rounds": [{"round": 9, "toolCallCount": 0}]}
        )
    with pytest.raises(ValueError, match="round metric"):
        sync_run_logs._safe_rounds(  # pyright: ignore[reportPrivateUsage]
            {"rounds": [{"round": 1, "toolCallCount": 0, "inputTokens": -1}]}
        )


def test_tool_export_skips_incomplete_calls_and_rejects_unsafe_names() -> None:
    outcome = {"rounds": [{"round": 1, "toolCallCount": 2, "toolCalls": [
        {"toolCallId": "first", "toolName": "zendesk.get_ticket", "status": "unknown"},
        {"toolCallId": "second", "toolName": "github.search_code", "status": "succeeded"},
    ]}]}
    assert [call["tool_name"] for call in sync_run_logs._safe_tool_calls(outcome)[0]] == [  # pyright: ignore[reportPrivateUsage]
        "github.search_code"
    ]
    outcome["rounds"][0]["toolCalls"][1]["toolName"] = "private key with spaces"  # type: ignore[index]
    with pytest.raises(ValueError, match="tool telemetry"):
        sync_run_logs._safe_tool_calls(outcome)  # pyright: ignore[reportPrivateUsage]


def test_content_backfill_targets_existing_rounds_and_matches_tool_ids() -> None:
    row = _row(
        remote_run_id=10267,
        rounds_reported=2,
        round_remote_ids_json='{"1": 100, "2": 101}',
        tool_calls_reported_json='{"1": 1}',
    )
    outcome = json.loads(row["outcome_json"])
    outcome["rounds"][0]["promptContent"] = '{"input":"provider request"}'
    outcome["rounds"][0]["assistantContent"] = "Looking up the ticket"
    outcome["rounds"][0]["toolCalls"] = [
        {"toolCallId": "call-1", "toolName": "custom_mcp.list_tools", "status": "succeeded"}
    ]
    row["outcome_json"] = json.dumps(outcome)
    row["tool_call_records_json"] = json.dumps([
        {"type": "tool_call", "toolCallId": "other", "arguments": {"wrong": True},
         "result": "wrong result"},
        {"type": "tool_call", "toolCallId": "call-1", "arguments": {"scope": "all"},
         "result": "complete result"},
    ])

    payload = sync_run_logs.to_content_payload(row)

    assert payload["task_preview"] == "Full initial prompt"
    rounds = cast(list[dict[str, Any]], payload["rounds"])
    assert rounds[0]["prompt_content"] == '{"input":"provider request"}'
    assert rounds[0]["assistant_content"] == "Looking up the ticket"
    assert rounds[0]["tool_calls"] == [
        {"tool_name": "custom_mcp.list_tools", "arguments": {"scope": "all"},
         "result_preview": "complete result"}
    ]
    assert rounds[1]["tool_calls"] == []


def test_content_backfill_rejects_partial_export() -> None:
    row = _row(remote_run_id=10267, rounds_reported=1)
    with pytest.raises(ValueError, match="not fully exported"):
        sync_run_logs.to_content_payload(row)


def test_credentials_require_https_origin(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"base_url": "http://example.com", "api_key": "key"}))
    with pytest.raises(ValueError, match="HTTPS origin"):
        sync_run_logs.load_credentials(credentials)
    credentials.write_text(json.dumps({"base_url": "https://example.com/api", "api_key": "key"}))
    with pytest.raises(ValueError, match="origin"):
        sync_run_logs.load_credentials(credentials)


def test_checkpoint_sql_binds_round_index_as_integer_before_json_key() -> None:
    # The index is passed as int by asyncpg. Casting it directly to text makes
    # SAVE_ROUND_SQL infer conflicting types and SAVE_TOOL_SQL expect a str.
    assert "ARRAY[($2::int)::text]" in sync_run_logs.SAVE_ROUND_SQL
    assert "ARRAY[($2::int)::text]" in sync_run_logs.SAVE_TOOL_SQL


class _Response:
    def __init__(self, result: object = 42) -> None:
        self.result = result

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"id": self.result}


class _Connection:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.checkpoints: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, *_: object) -> list[dict[str, Any]]:
        return [self.row]

    async def execute(self, sql: str, *args: object) -> str:
        self.checkpoints.append((sql, args))
        return "INSERT 0 1" if sql == sync_run_logs.CLAIM_CREATE_SQL else "UPDATE 1"


class _Client:
    def __init__(self, *, bad_round_ack: bool = False, bad_tool_ack: bool = False) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.bad_round_ack = bad_round_ack
        self.bad_tool_ack = bad_tool_ack

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
        assert headers == {"X-Agent-Key": "test-key"}
        self.requests.append(("POST", url, json))
        bad_ack = ((self.bad_round_ack and url.endswith("/rounds"))
                   or (self.bad_tool_ack and url.endswith("/tool-calls")))
        return _Response(None if bad_ack else 42)

    async def put(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
        assert headers == {"X-Agent-Key": "test-key"}
        self.requests.append(("PUT", url, json))
        return _Response()


@pytest.mark.asyncio
async def test_create_rounds_finish_are_checkpointed_in_order() -> None:
    connection = _Connection(_row())
    client = _Client()
    result = await sync_run_logs.export_batch(
        connection,  # pyright: ignore[reportArgumentType]
        client,  # pyright: ignore[reportArgumentType]
        sync_run_logs.ExportCredentials("https://example.com", "test-key"), limit=10,
    )
    assert result == (1, 0)
    assert [method for method, _, _ in client.requests] == ["POST", "POST", "POST", "PUT"]
    assert client.requests[0][1] == "https://example.com/api/agent/logs/runs"
    assert client.requests[1][1].endswith("/42/rounds")
    assert client.requests[-1][1].endswith("/42")
    assert [args for _, args in connection.checkpoints] == [
        ("job-1",), ("job-1", 42),
        ("job-1", "round:1", 42), ("job-1", 1, 42, "round:1", 42),
        ("job-1", "round:2", 42), ("job-1", 2, 42, "round:2", 42),
        ("job-1", connection.row["source_updated_at"], 42),
    ]


@pytest.mark.asyncio
async def test_resume_from_round_checkpoint_without_recreating_run() -> None:
    connection = _Connection(_row(
        remote_run_id=42, rounds_reported=1, round_remote_ids_json='{"1": 41}',
    ))
    client = _Client()
    await sync_run_logs.export_row(
        connection,  # pyright: ignore[reportArgumentType]
        client,  # pyright: ignore[reportArgumentType]
        sync_run_logs.ExportCredentials("https://example.com", "test-key"), connection.row,
    )
    assert [method for method, _, _ in client.requests] == ["POST", "PUT"]
    assert connection.checkpoints[0][1] == ("job-1", "round:2", 42)


@pytest.mark.asyncio
async def test_exports_tool_content_under_its_matching_remote_round() -> None:
    row = _row()
    outcome = json.loads(row["outcome_json"])
    outcome["rounds"][0]["toolCalls"] = [
        {"toolCallId": "call-1", "toolName": "zendesk.get_ticket",
         "status": "succeeded", "durationMs": 47,
         "arguments": {"api_key": "secret-key"}, "result": "customer-private"},
        {"toolCallId": "call-2", "toolName": "github.search_code",
         "status": "failed", "durationMs": 18, "error": "private-error"},
    ]
    row["outcome_json"] = json.dumps(outcome)
    row["tool_call_records_json"] = json.dumps([
        {"type": "tool_call", "toolCallId": "call-1", "arguments": {"query": "private-query"},
         "result": {"data": "customer-private"}},
        {"type": "tool_call", "toolCallId": "call-2", "arguments": [1, 2],
         "result": "private-error"},
    ])
    connection = _Connection(row)
    client = _Client()
    result = await sync_run_logs.export_batch(
        connection,  # pyright: ignore[reportArgumentType]
        client,  # pyright: ignore[reportArgumentType]
        sync_run_logs.ExportCredentials("https://example.com", "test-key"), limit=10,
    )
    assert result == (1, 0)
    assert [method for method, _, _ in client.requests] == ["POST"] * 5 + ["PUT"]
    tool_requests = [request for request in client.requests if request[1].endswith("/tool-calls")]
    assert len(tool_requests) == 2
    assert all(url.endswith("/42/rounds/42/tool-calls") for _, url, _ in tool_requests)
    assert tool_requests[0][2] == {
        "tool_name": "zendesk.get_ticket", "arguments": {"query": "private-query"},
        "result_preview": '{"data": "customer-private"}',
        "success": True, "error_message": None, "duration_ms": 47,
    }
    assert tool_requests[1][2]["success"] is False
    assert tool_requests[1][2]["arguments"] == {"value": [1, 2]}
    assert tool_requests[1][2]["result_preview"] == "private-error"
    assert ("job-1", 1, 1, 42, "tool:1:1") in [args for _, args in connection.checkpoints]


@pytest.mark.asyncio
async def test_resume_tool_export_without_recreating_round() -> None:
    row = _row(
        remote_run_id=42, rounds_reported=2,
        round_remote_ids_json='{"1": 51, "2": 52}',
        tool_calls_reported_json='{"1": 1}',
    )
    outcome = json.loads(row["outcome_json"])
    outcome["rounds"][0]["toolCalls"] = [
        {"toolCallId": "call-1", "toolName": "zendesk.get_ticket", "status": "succeeded"},
        {"toolCallId": "call-2", "toolName": "zendesk.list_attachments", "status": "succeeded"},
    ]
    row["outcome_json"] = json.dumps(outcome)
    connection = _Connection(row)
    client = _Client()
    await sync_run_logs.export_row(
        connection,  # pyright: ignore[reportArgumentType]
        client,  # pyright: ignore[reportArgumentType]
        sync_run_logs.ExportCredentials("https://example.com", "test-key"), row,
    )
    assert [method for method, _, _ in client.requests] == ["POST", "PUT"]
    assert client.requests[0][1].endswith("/42/rounds/51/tool-calls")
    assert client.requests[0][2]["tool_name"] == "zendesk.list_attachments"


@pytest.mark.asyncio
async def test_uncertain_tool_ack_is_held_for_manual_review() -> None:
    row = _row(remote_run_id=42, rounds_reported=1, round_remote_ids_json='{"1": 51}')
    outcome = json.loads(row["outcome_json"])
    outcome["rounds"] = [outcome["rounds"][0]]
    outcome["rounds"][0]["toolCalls"] = [
        {"toolCallId": "call-1", "toolName": "zendesk.get_ticket", "status": "succeeded"},
    ]
    row["outcome_json"] = json.dumps(outcome)
    connection = _Connection(row)
    client = _Client(bad_tool_ack=True)
    assert await sync_run_logs.export_batch(
        connection,  # pyright: ignore[reportArgumentType]
        client,  # pyright: ignore[reportArgumentType]
        sync_run_logs.ExportCredentials("https://example.com", "test-key"), limit=10,
    ) == (0, 1)
    assert [method for method, _, _ in client.requests] == ["POST"]
    assert connection.checkpoints[-1][1] == ("job-1", "tool:1:1", 42)


@pytest.mark.asyncio
async def test_invalid_ack_does_not_mark_round_or_run_finished() -> None:
    connection = _Connection(_row())
    client = _Client(bad_round_ack=True)
    result = await sync_run_logs.export_batch(
        connection,  # pyright: ignore[reportArgumentType]
        client,  # pyright: ignore[reportArgumentType]
        sync_run_logs.ExportCredentials("https://example.com", "test-key"), limit=10,
    )
    assert result == (0, 1)
    assert [args for _, args in connection.checkpoints] == [
        ("job-1",), ("job-1", 42), ("job-1", "round:1", 42),
    ]


@pytest.mark.asyncio
async def test_uncertain_post_is_not_retried_automatically() -> None:
    connection = _Connection(_row(remote_run_id=42, pending_operation="round:1"))
    client = _Client()
    result = await sync_run_logs.export_batch(
        connection,  # pyright: ignore[reportArgumentType]
        client,  # pyright: ignore[reportArgumentType]
        sync_run_logs.ExportCredentials("https://example.com", "test-key"), limit=10,
    )
    assert result == (0, 1)
    assert client.requests == []
