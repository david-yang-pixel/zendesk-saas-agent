from __future__ import annotations

from typing import Protocol, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from casework_ingress.app import create_app
from casework_ingress.fakes import (
    InMemoryCancelSink,
    InMemoryIdempotency,
    InMemoryJobSink,
)
from casework_ingress.ports import (
    AdmissionEvent,
    AdmissionResult,
    AdmissionUnavailable,
)
from casework_ingress.processing_rules import ProcessingRuleConfig


class _TypedTestClient(Protocol):
    """Typed subset of TestClient (its transitive httpx stubs are incomplete)."""

    def get(self, url: str) -> httpx.Response: ...

    def post(self, url: str, *, json: object) -> httpx.Response: ...


class _Admission:
    def __init__(self) -> None:
        self.events: list[AdmissionEvent] = []

    async def admit(self, event: AdmissionEvent) -> AdmissionResult:
        self.events.append(event)
        return AdmissionResult(
            action="enqueue",
            reason="eligible",
            ticket_id=event.normalized.ticket_id,
        )


class _UnavailableAdmission:
    async def admit(self, event: AdmissionEvent) -> AdmissionResult:
        raise AdmissionUnavailable("configuration_not_ready")


class _Readiness:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    async def check(self) -> bool:
        return self.ready


def _app() -> tuple[_TypedTestClient, InMemoryJobSink, InMemoryCancelSink]:
    jobs = InMemoryJobSink()
    cancellations = InMemoryCancelSink()
    app = create_app(
        config=ProcessingRuleConfig(
            saas_tag="saas",
            saas_assignee="saas-support",
        ),
        job_sink=jobs,
        cancel_sink=cancellations,
    )
    return cast(_TypedTestClient, TestClient(app)), jobs, cancellations


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": "evt-01",
        "ticket_id": 3726,
        "tags": ["priority", "SaaS"],
        "assignee": "saas-support",
        "status": "OPEN",
        "is_customer_message": True,
        "message_text": "The customer question",
        "untrusted_payload": {"must_not_reach_sink": True},
    }
    payload.update(overrides)
    return payload


def test_healthz_reports_ingress_ready() -> None:
    client, _, _ = _app()

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_fails_closed_without_postgres() -> None:
    client = cast(_TypedTestClient, TestClient(create_app(readiness=_Readiness(False))))

    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503


def test_webhook_fails_closed_when_durable_configuration_is_missing() -> None:
    client = cast(
        _TypedTestClient,
        TestClient(create_app(admission=_UnavailableAdmission())),
    )

    response = client.post("/webhook/zendesk", json=_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "configuration_not_ready"}


def test_metrics_reports_webhook_decisions_without_ticket_identifiers() -> None:
    client, _, _ = _app()
    client.post("/webhook/zendesk", json=_payload())
    client.post(
        "/webhook/zendesk",
        json=_payload(event_id="evt-ignore-metrics", tags=["priority"]),
    )

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert (
        'casework_ingress_webhooks_total{action="enqueue",reason="eligible"} 1'
        in response.text
    )
    assert (
        'casework_ingress_webhooks_total{action="ignore",reason="saas_tag_missing"} 1'
        in response.text
    )
    assert "3726" not in response.text


def test_matching_webhook_submits_only_ticket_id_and_event_metadata() -> None:
    client, jobs, cancellations = _app()

    response = client.post("/webhook/zendesk", json=_payload())

    assert response.status_code == 202
    assert response.json() == {
        "action": "enqueue",
        "reason": "eligible",
        "ticket_id": 3726,
    }
    assert len(jobs.submitted) == 1
    assert jobs.submitted[0].ticket_id == 3726
    assert jobs.submitted[0].event_id == "evt-01"
    assert cancellations.cancelled == []


def test_matching_webhook_uses_saas_group_when_individual_assignee_is_empty() -> None:
    jobs = InMemoryJobSink()
    app = create_app(
        config=ProcessingRuleConfig(
            saas_tag="saas",
            saas_assignee="40685275477140",
        ),
        job_sink=jobs,
    )
    client = cast(_TypedTestClient, TestClient(app))

    response = client.post(
        "/webhook/zendesk",
        json=_payload(
            event_id="evt-group-only",
            assignee=None,
            assignee_id=None,
            group_id="40685275477140",
        ),
    )

    assert response.status_code == 202
    assert response.json()["action"] == "enqueue"
    assert [job.ticket_id for job in jobs.submitted] == [3726]


def test_durable_admission_owns_the_complete_webhook_transaction() -> None:
    admission = _Admission()
    client = cast(
        _TypedTestClient,
        TestClient(create_app(admission=admission)),
    )

    response = client.post(
        "/webhook/zendesk",
        json=_payload(snapshot_cursor="messaging:latest-customer-message"),
    )

    assert response.json()["action"] == "enqueue"
    assert len(admission.events) == 1
    assert admission.events[0].event_id == "evt-01"
    assert admission.events[0].normalized.ticket_id == 3726
    assert admission.events[0].raw_payload["snapshot_cursor"] == (
        "messaging:latest-customer-message"
    )


def test_duplicate_matching_webhook_is_deduplicated_by_event_id() -> None:
    client, jobs, _ = _app()
    payload = _payload()

    first = client.post("/webhook/zendesk", json=payload)
    duplicate = client.post("/webhook/zendesk", json=payload)

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json() == {
        "action": "duplicate",
        "reason": "event_id_seen",
        "ticket_id": 3726,
    }
    assert len(jobs.submitted) == 1


def test_duplicate_claim_is_controlled_by_the_injected_idempotency_port() -> None:
    jobs = InMemoryJobSink()
    cancellations = InMemoryCancelSink()
    idempotency = InMemoryIdempotency()
    app = create_app(
        config=ProcessingRuleConfig(
            saas_tag="saas",
            saas_assignee="saas-support",
        ),
        job_sink=jobs,
        cancel_sink=cancellations,
        idempotency=idempotency,
    )
    client = cast(_TypedTestClient, TestClient(app))

    first = client.post("/webhook/zendesk", json=_payload())
    duplicate = client.post("/webhook/zendesk", json=_payload())

    assert first.status_code == duplicate.status_code == 202
    assert len(idempotency.seen_event_ids) == 1
    assert len(jobs.submitted) == 1


def test_human_webhook_cancels_without_submitting_a_job() -> None:
    client, jobs, cancellations = _app()

    response = client.post(
        "/webhook/zendesk",
        json=_payload(
            event_id="evt-human",
            tags=[],
            assignee="other-team",
            status="solved",
            message_text="  HuMaN  ",
        ),
    )

    assert response.status_code == 202
    assert response.json() == {
        "action": "cancel",
        "reason": "human",
        "ticket_id": 3726,
    }
    assert jobs.submitted == []
    assert len(cancellations.cancelled) == 1
    assert cancellations.cancelled[0].ticket_id == 3726
    assert cancellations.cancelled[0].event_id == "evt-human"


def test_ignored_webhook_does_not_write_to_either_sink() -> None:
    client, jobs, cancellations = _app()

    response = client.post(
        "/webhook/zendesk",
        json=_payload(event_id="evt-ignore", tags=["priority"]),
    )

    assert response.status_code == 202
    assert response.json() == {
        "action": "ignore",
        "reason": "saas_tag_missing",
        "ticket_id": 3726,
    }
    assert jobs.submitted == []
    assert cancellations.cancelled == []


@pytest.mark.parametrize(
    "missing_field",
    ["tags", "assignee", "status", "is_customer_message", "message_text"],
)
def test_missing_rule_field_fails_closed_without_sink_writes(
    missing_field: str,
) -> None:
    client, jobs, cancellations = _app()
    payload = _payload(event_id=f"evt-missing-{missing_field}")
    payload.pop(missing_field)

    response = client.post("/webhook/zendesk", json=payload)

    assert response.status_code == 202
    assert response.json()["action"] == "ignore"
    assert jobs.submitted == []
    assert cancellations.cancelled == []


def test_unknown_actor_cannot_trigger_human_cancellation() -> None:
    client, jobs, cancellations = _app()

    response = client.post(
        "/webhook/zendesk",
        json=_payload(
            event_id="evt-unknown-actor",
            actor="automation",
            visibility="public",
            message_text="human",
        ),
    )

    assert response.status_code == 202
    assert response.json()["action"] == "ignore"
    assert jobs.submitted == []
    assert cancellations.cancelled == []


def test_unknown_actor_shape_cannot_trigger_human_cancellation() -> None:
    client, jobs, cancellations = _app()

    response = client.post(
        "/webhook/zendesk",
        json=_payload(
            event_id="evt-unknown-actor-shape",
            actor={"type": "mystery"},
            visibility="public",
            message_text="human",
        ),
    )

    assert response.status_code == 202
    assert response.json()["action"] == "ignore"
    assert jobs.submitted == []
    assert cancellations.cancelled == []


def test_internal_customer_actor_message_cannot_trigger_human_cancellation() -> None:
    client, jobs, cancellations = _app()

    response = client.post(
        "/webhook/zendesk",
        json=_payload(
            event_id="evt-internal-message",
            actor="customer",
            visibility="internal",
            message_text="human",
        ),
    )

    assert response.status_code == 202
    assert response.json()["action"] == "ignore"
    assert jobs.submitted == []
    assert cancellations.cancelled == []
