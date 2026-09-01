from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from casework_admin.app import create_app
from casework_admin.config import (
    AdminConfig,
    InMemoryConfigRepository,
    ModelConfig,
    ProcessingRulesConfig,
)


class _Client(Protocol):
    def get(self, url: str) -> httpx.Response: ...

    def post(self, url: str) -> httpx.Response: ...

    def put(
        self, url: str, *, json: object, headers: dict[str, str]
    ) -> httpx.Response: ...


class _Readiness:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    async def check(self) -> bool:
        return self.ready


def _config() -> AdminConfig:
    return AdminConfig(
        desired_state="paused",
        root_model=ModelConfig(
            provider="openai", model="root", credential_ref="secret/root"
        ),
        rules=ProcessingRulesConfig(
            saas_tag="saas", saas_assignee="saas-support", reply_enabled=False
        ),
    )


def _client() -> _Client:
    repository = InMemoryConfigRepository(initial=_config())
    return cast(_Client, TestClient(create_app(repository=repository)))


def test_admin_health_and_fixed_policy_are_read_only() -> None:
    client = _client()

    assert client.get("/healthz").json() == {"status": "ok"}
    policy = client.get("/api/config/sandbox-policy")

    assert policy.status_code == 200
    assert policy.json()["attempt_1"]["cpu"] == 0.5
    assert policy.json()["attempt_2"]["cpu"] == 1.0


def test_admin_readiness_fails_closed_when_postgres_is_unavailable() -> None:
    unavailable = cast(
        _Client,
        TestClient(
            create_app(
                repository=InMemoryConfigRepository(initial=_config()),
                readiness=_Readiness(False),
            )
        ),
    )
    available = cast(
        _Client,
        TestClient(
            create_app(
                repository=InMemoryConfigRepository(initial=_config()),
                readiness=_Readiness(True),
            )
        ),
    )

    assert unavailable.get("/healthz").status_code == 200
    assert unavailable.get("/readyz").status_code == 503
    assert available.get("/readyz").json() == {"status": "ready"}


def test_config_update_requires_current_revision() -> None:
    client = _client()
    current = client.get("/api/config").json()
    body = {
        "expected_revision": current["revision"],
        "config": {**current["config"], "desired_state": "running"},
    }

    updated = client.put(
        "/api/config", json=body, headers={"X-Admin-Actor": "operator@example.com"}
    )
    stale = client.put(
        "/api/config", json=body, headers={"X-Admin-Actor": "operator@example.com"}
    )

    assert updated.status_code == 200
    assert updated.json()["config"]["desired_state"] == "running"
    assert updated.json()["actor"] == "operator@example.com"
    assert stale.status_code == 409


def test_admin_image_serves_built_web_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>Casework console</main>", encoding="utf-8")
    monkeypatch.setenv("ADMIN_WEB_DIST", str(dist))

    client = _client()

    response = client.get("/")
    assert response.status_code == 200
    assert "Casework console" in response.text


def test_admin_read_models_are_available_for_all_console_pages() -> None:
    client = _client()

    overview = client.get("/api/overview")
    runs = client.get("/api/runs")
    registry = client.get("/api/registry")

    assert overview.status_code == 200
    assert overview.json()["desired_state"] == "paused"
    assert {"metrics", "queue_health", "failures", "recent_runs"} <= set(
        overview.json()
    )
    assert runs.status_code == 200
    assert runs.json() == {"items": [], "total": 0}
    assert registry.status_code == 200
    assert registry.json() == {"skills": [], "tools": []}


def test_registry_connection_test_is_a_typed_read_only_probe() -> None:
    client = _client()

    response = client.post("/api/registry/tools/sql/test")

    assert response.status_code == 200
    assert response.json() == {
        "tool_id": "sql",
        "status": "untested",
        "latency_ms": None,
    }
