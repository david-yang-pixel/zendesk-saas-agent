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

    def put(
        self, url: str, *, json: object, headers: dict[str, str]
    ) -> httpx.Response: ...


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
