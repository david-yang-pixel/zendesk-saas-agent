"""Exercise webhook -> queue -> Sandbox -> Tool Loop -> dry-run delivery."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast


def _json_request(
    url: str, payload: object | None = None, *, internal_token: str | None = None
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method="GET" if payload is None else "POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {internal_token}"} if internal_token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise RuntimeError("probe endpoint returned a non-object response")
    return cast(dict[str, Any], result)


def probe(*, ticket_id: int, timeout_seconds: int, internal_token: str) -> str:
    query = urllib.parse.quote(str(ticket_id))
    before = _json_request(
        f"http://127.0.0.1:18104/api/runs?query={query}",
        internal_token=internal_token,
    )
    existing_sessions = _ticket_session_ids(before, ticket_id=ticket_id)
    event_id = f"greenfield-dry-run-{ticket_id}-{time.time_ns()}"
    admitted = _json_request(
        "http://127.0.0.1:18101/webhook/zendesk",
        {
            "event_id": event_id,
            "ticket_id": ticket_id,
            "tags": ["saas", "production"],
            "assignee_id": "46493567999892",
            "status": "open",
            "is_customer_message": True,
            "message_text": "Please investigate the latest question in this ticket.",
            "actor": "customer",
            "visibility": "public",
            "is_public": True,
        },
    )
    if admitted.get("action") != "enqueue":
        raise RuntimeError(f"dry-run event was not enqueued: {admitted.get('reason')}")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        runs = _json_request(
            f"http://127.0.0.1:18104/api/runs?query={query}",
            internal_token=internal_token,
        )
        items = runs.get("items")
        if isinstance(items, list):
            for raw in cast(list[object], items):
                if not isinstance(raw, dict):
                    continue
                item = cast(dict[str, object], raw)
                if item.get("ticket_id") != ticket_id:
                    continue
                session_id = item.get("session_id")
                if not isinstance(session_id, str) or session_id in existing_sessions:
                    continue
                status = item.get("status")
                if status in {"completed", "awaiting_customer"}:
                    print(
                        "Greenfield dry-run completed without a Zendesk write "
                        f"at {datetime.now(UTC).isoformat()}."
                    )
                    return session_id
                if status in {"failed", "cancelled", "human_takeover"}:
                    raise RuntimeError(f"dry-run reached terminal status {status}")
        time.sleep(3)
    raise RuntimeError("dry-run did not reach a safe terminal status before timeout")


def _ticket_session_ids(payload: dict[str, Any], *, ticket_id: int) -> set[str]:
    items = payload.get("items")
    if not isinstance(items, list):
        return set()
    return {
        session_id
        for raw in cast(list[object], items)
        if isinstance(raw, dict)
        and raw.get("ticket_id") == ticket_id
        and isinstance((session_id := raw.get("session_id")), str)
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticket-id", type=int, default=3726)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--internal-token-file", type=Path, required=True)
    args = parser.parse_args()
    probe(
        ticket_id=args.ticket_id,
        timeout_seconds=args.timeout_seconds,
        internal_token=args.internal_token_file.read_text(encoding="utf-8").strip(),
    )


if __name__ == "__main__":
    main()
