from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from casework_sandbox.service import create_app
from contracts.outcome import Outcome, OutcomeKind
from contracts.runtime import ModelBinding, RuntimeBootstrap
from contracts.sandbox import (
    SandboxCancelRequest,
    SandboxResources,
    SandboxStartRequest,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
BOOTSTRAP = RuntimeBootstrap(
    config_revision="config-1",
    root_model=ModelBinding(
        provider="openai", model="root", credential_ref="secret/root"
    ),
    model_digest="model-sha",
    skill_digest="skill-sha",
    tool_digest="tool-sha",
)


class _Client(Protocol):
    def post(self, url: str, *, json: object) -> httpx.Response: ...

    def get(self, url: str) -> httpx.Response: ...


class FakeCommands:
    def __init__(self) -> None:
        self.starts: list[SandboxStartRequest] = []
        self.cancels: list[SandboxCancelRequest] = []

    async def run(self, request: SandboxStartRequest) -> Outcome:
        self.starts.append(request)
        return Outcome(
            outcome_id="outcome-1",
            session_id=request.session_id,
            ticket_id=request.ticket_id,
            kind=OutcomeKind.ANSWER,
            answer="Evidence-backed answer",
            evidence_ids=("ev-1",),
        )

    async def cancel(self, request: SandboxCancelRequest) -> None:
        self.cancels.append(request)


def _start() -> SandboxStartRequest:
    return SandboxStartRequest(
        sandbox_id="sandbox-1",
        job_id="job-1",
        session_id="session-1",
        ticket_id=3726,
        generation=7,
        resource_attempt=1,
        deadline=NOW,
        resources=SandboxResources(),
        config_revision="config-1",
        model_digest="model-sha",
        skill_digest="skill-sha",
        tool_digest="tool-sha",
        bootstrap=BOOTSTRAP,
    )


def test_start_run_uses_versioned_contract_and_returns_typed_result() -> None:
    commands = FakeCommands()
    client = cast(_Client, TestClient(create_app(commands=commands)))

    response = client.post("/v1/runs", json=_start().model_dump(mode="json"))

    assert response.status_code == 200
    assert response.json()["outcome"]["kind"] == "answer"
    assert commands.starts[0].ticket_id == 3726


def test_cancel_run_reaches_same_sandbox_session() -> None:
    commands = FakeCommands()
    client = cast(_Client, TestClient(create_app(commands=commands)))
    cancel = SandboxCancelRequest(
        sandbox_id="sandbox-1",
        session_id="session-1",
        generation=7,
        reason="human takeover",
        requested_at=NOW,
    )

    response = client.post("/v1/cancel", json=cancel.model_dump(mode="json"))

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert commands.cancels == [cancel]


def test_default_factory_fails_closed_without_external_adapter_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "ZENDESK_BASE_URL",
        "ZENDESK_EMAIL",
        "ZENDESK_API_TOKEN",
        "CASEWORK_MODEL_BASE_URL",
        "CASEWORK_MODEL_API_KEY",
        "CASEWORK_MODEL_NAME",
        "CASEWORK_TOOL_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)

    from casework_sandbox.composition import SandboxConfigurationError

    with pytest.raises(SandboxConfigurationError):
        create_app()


def test_unconfigured_commands_are_not_ready_and_cannot_start() -> None:
    # Explicit dependency injection remains available to tests and local
    # composition. The production default must fail closed instead.
    from casework_sandbox.service import UnconfiguredSandboxCommands

    commands = UnconfiguredSandboxCommands()
    client = cast(_Client, TestClient(create_app(commands=commands)))

    assert client.get("/readyz").status_code == 503
    response = client.post("/v1/runs", json=_start().model_dump(mode="json"))
    assert response.status_code == 503
