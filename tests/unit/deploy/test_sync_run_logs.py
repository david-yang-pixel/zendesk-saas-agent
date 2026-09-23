from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from deploy import sync_run_logs


def _row(**overrides: object) -> dict[str, Any]:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    values: dict[str, Any] = {
        "job_id": "job-1", "ticket_id": 3726, "status": "completed",
        "created_at": now - timedelta(minutes=3), "job_finished_at": now,
        "event_type": "ticket.mention", "started_at": now - timedelta(minutes=2),
        "run_finished_at": now, "model": "gpt-5.6-terra",
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
        "remote_run_id": None, "rounds_reported": 0, "pending_operation": None,
        "source_updated_at": now,
    }
    values.update(overrides)
    return values


def test_payloads_use_existing_api_and_only_bounded_metrics() -> None:
    row = _row()
    outcome = sync_run_logs._outcome(row)  # pyright: ignore[reportPrivateUsage]
    rounds = sync_run_logs._safe_rounds(outcome)  # pyright: ignore[reportPrivateUsage]
    create = sync_run_logs.to_create_payload(row)
    finish = sync_run_logs.to_finish_payload(row, outcome, rounds)

    assert create["source"] == "saas"
    assert create["trigger_type"] == "mention"
    assert len(str(create["session_id"])) == 32
    assert finish["total_rounds"] == 2
    assert finish["total_input_tokens"] == 75
    assert finish["total_output_tokens"] == 12
    assert rounds is not None
    assert rounds[0]["assistant_content"] is None
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


def test_credentials_require_https_origin(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"base_url": "http://example.com", "api_key": "key"}))
    with pytest.raises(ValueError, match="HTTPS origin"):
        sync_run_logs.load_credentials(credentials)
    credentials.write_text(json.dumps({"base_url": "https://example.com/api", "api_key": "key"}))
    with pytest.raises(ValueError, match="origin"):
        sync_run_logs.load_credentials(credentials)


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
    def __init__(self, *, bad_round_ack: bool = False) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.bad_round_ack = bad_round_ack

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
        assert headers == {"X-Agent-Key": "test-key"}
        self.requests.append(("POST", url, json))
        return _Response(None if self.bad_round_ack and url.endswith("/rounds") else 42)

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
        ("job-1", "round:1", 42), ("job-1", 1, 42, "round:1"),
        ("job-1", "round:2", 42), ("job-1", 2, 42, "round:2"),
        ("job-1", connection.row["source_updated_at"], 42),
    ]


@pytest.mark.asyncio
async def test_resume_from_round_checkpoint_without_recreating_run() -> None:
    connection = _Connection(_row(remote_run_id=42, rounds_reported=1))
    client = _Client()
    await sync_run_logs.export_row(
        connection,  # pyright: ignore[reportArgumentType]
        client,  # pyright: ignore[reportArgumentType]
        sync_run_logs.ExportCredentials("https://example.com", "test-key"), connection.row,
    )
    assert [method for method, _, _ in client.requests] == ["POST", "PUT"]
    assert connection.checkpoints[0][1] == ("job-1", "round:2", 42)


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
