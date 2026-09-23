from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from casework_ingress.app import create_app
from casework_ingress.ports import AdmissionUnavailable
from casework_ingress.postgres_admission import PostgresAdmission
from casework_persistence.repositories import ConfigRevisionRepository
from tests.unit.ingress.test_postgres_admission import CONFIG, _SessionAdapter

SECRET = "independent-ticket-events-secret"
NOW = 1780460000
PATH = "/webhook/zendesk-ticket-events"


def payload() -> dict[str, Any]:
    return {
        "id": "evt-ticket-1",
        "type": "zen:event-type:ticket.comment_added",
        "detail": {"id": "99999"},
        "event": {"comment": {
            "id": "1234", "body": "@agent investigate private-fixture",
            "is_public": False, "author": {"id": "5678", "is_staff": True},
        }},
    }


def signed(client: TestClient, data: Any, *, timestamp: int = NOW, secret: str = SECRET):
    body = data if isinstance(data, bytes) else json.dumps(data, indent=2).encode()
    signature = base64.b64encode(hmac.new(
        secret.encode(), str(timestamp).encode() + body, hashlib.sha256
    ).digest()).decode()
    return client.post(PATH, content=body, headers={
        "X-Zendesk-Webhook-Signature": signature,
        "X-Zendesk-Webhook-Signature-Timestamp": str(timestamp),
    })


@pytest.fixture
async def client(tmp_path):
    sessions = _SessionAdapter(tmp_path / "ticket-events.sqlite")
    async with sessions.transaction() as session:
        await ConfigRevisionRepository(session).bootstrap(
            revision="cfg-1", config=CONFIG, actor="test"
        )
    return TestClient(create_app(
        admission=PostgresAdmission(transaction_scope=sessions.transaction),
        ticket_events_signing_secret=SECRET,
        messaging_signing_secret="different-messaging-secret",
        messaging_clock=lambda: NOW,
    ))


def test_signed_note_enqueues_and_duplicate_does_not(client, caplog):
    first = signed(client, payload())
    second = signed(client, payload())
    assert first.status_code == second.status_code == 202
    assert first.json()["action"] == "enqueue"
    assert second.json()["action"] == "duplicate"
    assert "private-fixture" not in caplog.text + first.text + second.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("change", ["public", "customer", "ordinary", "agent_draft", "other_type"])
def test_ignored_events(client, change):
    data = payload()
    comment = data["event"]["comment"]
    if change == "public":
        comment["is_public"] = True
    elif change == "customer":
        comment["author"]["is_staff"] = False
    elif change == "ordinary":
        comment["body"] = "ordinary note"
    elif change == "agent_draft":
        comment["body"] = "[DIFY_AI_AGENT_DRAFT:test-job] Please ask @David Yang to review."
    else:
        data = {"type": "zen:event-type:ticket.status_changed"}
    response = signed(client, data)
    assert response.status_code == 202
    assert response.json()["action"] == "ignore"
    assert signed(client, payload()).json()["action"] == "enqueue"


@pytest.mark.parametrize("change", ["id", "ticket", "comment", "author", "public_type", "staff_type", "body", "json"])
def test_malformed_events(client, change):
    data = payload()
    if change == "id":
        del data["id"]
    elif change == "ticket":
        del data["detail"]["id"]
    elif change == "comment":
        data["event"] = {}
    elif change == "author":
        del data["event"]["comment"]["author"]["id"]
    elif change == "public_type":
        data["event"]["comment"]["is_public"] = "false"
    elif change == "staff_type":
        data["event"]["comment"]["author"]["is_staff"] = 1
    elif change == "body":
        del data["event"]["comment"]["body"]
    response = signed(client, b'{"private-fixture"' if change == "json" else data)
    assert response.status_code == 400
    assert response.json() == {"detail": "bad_payload"}


def test_invalid_signatures(client):
    assert signed(client, payload(), secret="wrong").status_code == 401
    assert signed(client, payload(), secret="different-messaging-secret").status_code == 401
    assert signed(client, payload(), timestamp=NOW - 301).status_code == 401
    assert signed(client, payload(), timestamp=NOW + 301).status_code == 401
    assert client.post(PATH, json=payload()).status_code == 401


@pytest.mark.parametrize("configured", [True, False])
def test_unavailable_admission(configured):
    class Unavailable:
        async def admit(self, event):
            raise AdmissionUnavailable("private-fixture")
    client = TestClient(create_app(
        admission=Unavailable() if configured else None,
        ticket_events_signing_secret=SECRET, messaging_clock=lambda: NOW,
    ))
    response = signed(client, payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "admission_unavailable"}


def test_missing_secret_does_not_break_health_but_rejects_events():
    client = TestClient(create_app(messaging_clock=lambda: NOW))
    assert client.get("/healthz").status_code == 200
    assert signed(client, payload()).status_code == 401
