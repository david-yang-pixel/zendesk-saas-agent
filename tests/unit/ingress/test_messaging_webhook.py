from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Protocol, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from casework_ingress.app import create_app
from casework_ingress.fakes import InMemoryCancelSink, InMemoryJobSink
from casework_ingress.messaging import (
    MessagingEvent,
    parse_messaging_payload,
    verify_zendesk_signature,
)
from casework_ingress.ports import AdmissionAction, AdmissionEvent, AdmissionResult
from casework_ingress.processing_rules import ProcessingRuleConfig
from casework_ingress.zendesk import ZendeskApiTicketReader, ZendeskTicket


class _TypedTestClient(Protocol):
    def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
    ) -> httpx.Response: ...


class _TicketReader:
    def __init__(self, ticket: ZendeskTicket) -> None:
        self.ticket = ticket
        self.ticket_ids: list[int] = []

    async def fetch_ticket(self, ticket_id: int) -> ZendeskTicket:
        self.ticket_ids.append(ticket_id)
        return self.ticket


class _Admission:
    def __init__(
        self, action: AdmissionAction = "enqueue", reason: str = "eligible"
    ) -> None:
        self.action: AdmissionAction = action
        self.reason = reason
        self.events: list[AdmissionEvent] = []

    async def admit(self, event: AdmissionEvent) -> AdmissionResult:
        self.events.append(event)
        return AdmissionResult(
            action=self.action,
            reason=self.reason,
            ticket_id=event.normalized.ticket_id,
        )


SECRET = "messaging-secret"
TIMESTAMP = "1780460000"


def _payload(
    *,
    event_id: str = "evt-1",
    actor_type: str = "end_user",
    body: str = "please investigate",
) -> dict[str, object]:
    return {
        "id": event_id,
        "type": "zen:event-type:messaging_ticket.message_added",
        "detail": {"id": "2757"},
        "event": {
            "actor": {"type": actor_type},
            "message": {"id": "msg-1", "body": body, "public": True},
        },
        "subject": "zen:ticket:2757",
        "token": "must-not-be-persisted",
    }


def _signed_request(
    client: _TypedTestClient,
    payload: dict[str, object],
    *,
    timestamp: str = TIMESTAMP,
    secret: str = SECRET,
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = base64.b64encode(
        hmac.new(
            secret.encode(), timestamp.encode() + body, hashlib.sha256
        ).digest()
    ).decode()
    return client.post(
        "/webhook/zendesk-messaging",
        content=body,
        headers={
            "X-Zendesk-Webhook-Signature": signature,
            "X-Zendesk-Webhook-Signature-Timestamp": timestamp,
        },
    )


def _client(
    *,
    reader: _TicketReader | None = None,
    admission: _Admission | None = None,
    clock: float = 1780460000.0,
) -> tuple[_TypedTestClient, _TicketReader | None, _Admission | None]:
    app = create_app(
        ticket_reader=reader,
        admission=admission,
        messaging_signing_secret=SECRET,
        messaging_clock=lambda: clock,
    )
    return cast(_TypedTestClient, TestClient(app)), reader, admission


def test_signature_uses_timestamp_and_raw_body_and_rejects_stale() -> None:
    body = b'{"id":"evt-1"}'
    signature = base64.b64encode(
        hmac.new(SECRET.encode(), TIMESTAMP.encode() + body, hashlib.sha256).digest()
    ).decode()

    assert verify_zendesk_signature(
        secret=SECRET,
        timestamp=TIMESTAMP,
        body=body,
        signature=signature,
        now=1780460000.0,
    )
    assert not verify_zendesk_signature(
        secret=SECRET,
        timestamp=TIMESTAMP,
        body=body,
        signature=signature,
        now=1780460301.0,
    )


def test_raw_payload_extracts_customer_event_without_retaining_full_body() -> None:
    event = parse_messaging_payload(_payload())

    assert event == MessagingEvent(
        event_id="evt-1",
        ticket_id=2757,
        actor_type="end_user",
        message_id="msg-1",
        message_text="please investigate",
        public=True,
    )
    assert event.safe_payload() == {
        "event_id": "evt-1",
        "ticket_id": 2757,
        "actor_type": "end_user",
        "message_id": "msg-1",
        "snapshot_cursor": "messaging:msg-1",
        "public": True,
    }


def test_customer_raw_webhook_fetches_ticket_and_admits_minimal_projection() -> None:
    reader = _TicketReader(
        ZendeskTicket(
            ticket_id=2757,
            tags=("saas",),
            group_id=123,
            status="open",
        )
    )
    admission = _Admission()
    client, _, _ = _client(reader=reader, admission=admission)

    response = _signed_request(client, _payload())

    assert response.status_code == 202
    assert response.json() == {
        "action": "enqueue",
        "reason": "eligible",
        "ticket_id": 2757,
    }
    assert reader.ticket_ids == [2757]
    assert admission is not None
    assert len(admission.events) == 1
    normalized = admission.events[0].normalized
    assert normalized.ticket_id == 2757
    assert normalized.tags == ("saas",)
    assert normalized.assignee == 123
    assert normalized.status == "open"
    assert normalized.message_text == "please investigate"
    assert admission.events[0].raw_payload == {
        "event_id": "evt-1",
        "ticket_id": 2757,
        "actor_type": "end_user",
        "message_id": "msg-1",
        "snapshot_cursor": "messaging:msg-1",
        "public": True,
    }


def test_agent_actor_is_ignored_without_ticket_api_or_admission() -> None:
    reader = _TicketReader(
        ZendeskTicket(ticket_id=2757, tags=("saas",), group_id=123, status="open")
    )
    admission = _Admission()
    client, _, _ = _client(reader=reader, admission=admission)

    response = _signed_request(client, _payload(actor_type="agent"))

    assert response.status_code == 202
    assert response.json()["action"] == "ignore"
    assert reader.ticket_ids == []
    assert admission.events == []


def test_unknown_actor_shape_is_ignored_without_inspecting_message() -> None:
    reader = _TicketReader(
        ZendeskTicket(ticket_id=2757, tags=("saas",), group_id=123, status="open")
    )
    admission = _Admission()
    client, _, _ = _client(reader=reader, admission=admission)

    response = _signed_request(
        client,
        {
            **_payload(actor_type="agent"),
            "event": {
                "actor": ["unknown"],
                "message": ["not-an-object"],
            },
        },
    )

    assert response.status_code == 202
    assert response.json()["action"] == "ignore"
    assert reader.ticket_ids == []
    assert admission.events == []


def test_human_customer_message_is_admitted_as_cancel_only() -> None:
    reader = _TicketReader(
        ZendeskTicket(ticket_id=2757, tags=(), group_id=None, status="solved")
    )
    admission = _Admission(action="cancel", reason="human")
    client, _, _ = _client(reader=reader, admission=admission)

    response = _signed_request(client, _payload(body="  HuMaN  "))

    assert response.status_code == 202
    assert response.json()["action"] == "cancel"
    assert admission.events[0].normalized.message_text == "  HuMaN  "


def test_raw_human_message_uses_cancel_sink_without_creating_a_job() -> None:
    reader = _TicketReader(
        ZendeskTicket(ticket_id=2757, tags=(), group_id=None, status="solved")
    )
    jobs = InMemoryJobSink()
    cancellations = InMemoryCancelSink()
    app = create_app(
        config=ProcessingRuleConfig(
            saas_tag="saas", saas_assignee="saas-support"
        ),
        ticket_reader=reader,
        job_sink=jobs,
        cancel_sink=cancellations,
        messaging_signing_secret=SECRET,
        messaging_clock=lambda: 1780460000.0,
    )
    client = cast(_TypedTestClient, TestClient(app))

    response = _signed_request(client, _payload(body="human"))

    assert response.status_code == 202
    assert response.json()["action"] == "cancel"
    assert jobs.submitted == []
    assert len(cancellations.cancelled) == 1
    assert cancellations.cancelled[0].ticket_id == 2757


def test_raw_matching_message_submits_only_ticket_id_to_job_sink() -> None:
    reader = _TicketReader(
        ZendeskTicket(
            ticket_id=2757,
            tags=("saas",),
            group_id="saas-support",
            status="open",
        )
    )
    jobs = InMemoryJobSink()
    cancellations = InMemoryCancelSink()
    app = create_app(
        config=ProcessingRuleConfig(
            saas_tag="saas", saas_assignee="saas-support"
        ),
        ticket_reader=reader,
        job_sink=jobs,
        cancel_sink=cancellations,
        messaging_signing_secret=SECRET,
        messaging_clock=lambda: 1780460000.0,
    )
    client = cast(_TypedTestClient, TestClient(app))

    response = _signed_request(client, _payload())

    assert response.json()["action"] == "enqueue"
    assert jobs.submitted[0].ticket_id == 2757
    assert jobs.submitted[0].event_id == "evt-1"
    assert cancellations.cancelled == []


def test_bad_signature_and_ticket_api_failure_do_not_admit() -> None:
    reader = _TicketReader(
        ZendeskTicket(ticket_id=2757, tags=("saas",), group_id=123, status="open")
    )
    admission = _Admission()
    client, _, _ = _client(reader=reader, admission=admission)
    payload = _payload()
    body = json.dumps(payload, separators=(",", ":")).encode()

    bad = client.post(
        "/webhook/zendesk-messaging",
        content=body,
        headers={
            "X-Zendesk-Webhook-Signature": "wrong",
            "X-Zendesk-Webhook-Signature-Timestamp": TIMESTAMP,
        },
    )

    assert bad.status_code == 401
    assert reader.ticket_ids == []
    assert admission.events == []


def test_non_public_customer_message_is_ignored_without_ticket_lookup() -> None:
    reader = _TicketReader(
        ZendeskTicket(ticket_id=2757, tags=("saas",), group_id=123, status="open")
    )
    client, _, _ = _client(reader=reader, admission=_Admission())

    response = _signed_request(
        client,
        {
            **_payload(),
            "event": {
                "actor": {"type": "end_user"},
                "message": {
                    "id": "msg-1",
                    "body": "human",
                    "public": False,
                },
            },
        },
    )

    assert response.status_code == 202
    assert response.json()["action"] == "ignore"
    assert reader.ticket_ids == []


def test_invalid_json_is_rejected_without_echoing_body() -> None:
    client, _, _ = _client()
    body = b'{"token":"secret-value"'
    signature = base64.b64encode(
        hmac.new(SECRET.encode(), TIMESTAMP.encode() + body, hashlib.sha256).digest()
    ).decode()

    response = client.post(
        "/webhook/zendesk-messaging",
        content=body,
        headers={
            "X-Zendesk-Webhook-Signature": signature,
            "X-Zendesk-Webhook-Signature-Timestamp": TIMESTAMP,
        },
    )

    assert response.status_code == 400
    assert "secret-value" not in response.text


@pytest.mark.asyncio
async def test_zendesk_ticket_reader_uses_read_only_projection() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "ticket": {
                    "id": 2757,
                    "tags": ["saas", "priority"],
                    "group_id": 40685275477140,
                    "assignee_id": None,
                    "status": "open",
                    "description": "sensitive content is not projected",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    reader = ZendeskApiTicketReader(
        base_url="https://tenant.zendesk.com",
        email="support@example.com",
        api_token="api-token",
        client=client,
    )

    ticket = await reader.fetch_ticket(2757)
    await client.aclose()

    assert ticket == ZendeskTicket(
        ticket_id=2757,
        tags=("saas", "priority"),
        group_id=40685275477140,
        status="open",
        assignee_id=None,
    )
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == (
        "https://tenant.zendesk.com/api/v2/tickets/2757.json"
    )
