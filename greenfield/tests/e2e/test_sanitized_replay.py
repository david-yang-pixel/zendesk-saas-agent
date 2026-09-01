from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, cast

import httpx
from fastapi.testclient import TestClient

from casework_ingress.app import create_app
from casework_ingress.fakes import InMemoryCancelSink, InMemoryJobSink
from casework_ingress.processing_rules import ProcessingRuleConfig


class _TypedTestClient(Protocol):
    def post(self, url: str, *, json: object) -> httpx.Response: ...


def test_sanitized_zendesk_event_replay_matches_expected_decisions() -> None:
    jobs = InMemoryJobSink()
    cancellations = InMemoryCancelSink()
    client = cast(
        _TypedTestClient,
        TestClient(
            create_app(
                config=ProcessingRuleConfig(
                    saas_tag="saas",
                    saas_assignee="saas-support",
                ),
                job_sink=jobs,
                cancel_sink=cancellations,
            )
        ),
    )
    fixture = Path(__file__).parents[1] / "fixtures/replay/zendesk_events.json"
    cases = cast(list[dict[str, object]], json.loads(fixture.read_text()))

    observed: dict[str, str] = {}
    for case in cases:
        response = client.post("/webhook/zendesk", json=case["payload"])
        body = cast(dict[str, object], response.json())
        observed[str(case["name"])] = str(body["action"])

    assert observed == {
        str(case["name"]): str(case["expected_action"]) for case in cases
    }
    assert [job.ticket_id for job in jobs.submitted] == [9001]
    assert [cancel.ticket_id for cancel in cancellations.cancelled] == [9003]
