from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from casework_admin.app import create_app
from casework_admin.auth import AdminAuthStore, hash_password
from casework_admin.config import (
    AdminConfig,
    InMemoryConfigRepository,
    ModelConfig,
    ProcessingRulesConfig,
)
from casework_admin.model_credentials import (
    FileCredentialStore,
    ModelCatalogResponse,
    ModelCredentialService,
    ModelProbeResult,
    parse_credential_file,
)
from casework_admin.read_models import (
    ConnectionProbeResponse,
    InMemoryAdminReadRepository,
    RegistryResponse,
    RunRecord,
    ToolRecord,
)


class _Client(Protocol):
    def get(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> httpx.Response: ...

    def post(
        self,
        url: str,
        *,
        json: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response: ...

    def put(
        self,
        url: str,
        *,
        json: object,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response: ...


class _Readiness:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    async def check(self) -> bool:
        return self.ready


class _ModelProbe:
    def __init__(self, result: ModelProbeResult) -> None:
        self.result = result
        self.secrets: list[str] = []

    async def validate(
        self, *, provider: str, base_url: str, model: str, secret: str
    ) -> ModelProbeResult:
        assert provider == "openai"
        assert base_url == "https://api.openai.com"
        assert model == "root"
        self.secrets.append(secret)
        return self.result

    async def discover(
        self, *, provider: str, base_url: str, secret: str
    ) -> ModelCatalogResponse:
        assert provider == "openai"
        self.secrets.append(secret)
        return ModelCatalogResponse(
            provider=provider,
            base_url=base_url.rstrip("/v1"),
            models=("gpt-a", "gpt-b"),
            latency_ms=11,
        )


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


def _authenticated_client(tmp_path: Path, *, force_change: bool = False) -> _Client:
    auth_file = tmp_path / "admin_auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "users": {
                    "admin": {
                        "password": hash_password("correct-password"),
                        "force_password_change": force_change,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return cast(
        _Client,
        TestClient(
            create_app(
                repository=InMemoryConfigRepository(initial=_config()),
                auth_store=AdminAuthStore(auth_file),
                internal_token="internal-token-with-at-least-thirty-two-characters",
                public_origin="https://admin.example.com",
                session_cookie_path="/",
                secure_cookie=False,
            )
        ),
    )


def test_admin_api_requires_session_and_csrf(tmp_path: Path) -> None:
    client = _authenticated_client(tmp_path)

    assert client.get("/api/config").status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "correct-password"},
        ).status_code
        == 403
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct-password"},
        headers={"Origin": "https://admin.example.com"},
    )

    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    csrf = login.json()["csrf_token"]
    current = client.get("/api/config")
    update = {
        "expected_revision": current.json()["revision"],
        "config": current.json()["config"],
    }
    assert (
        client.put(
            "/api/config",
            json=update,
            headers={"Origin": "https://admin.example.com"},
        ).status_code
        == 403
    )
    saved = client.put(
        "/api/config",
        json=update,
        headers={
            "Origin": "https://admin.example.com",
            "X-CSRF-Token": csrf,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["actor"] == "admin"


def test_internal_deployment_token_bypasses_browser_session(tmp_path: Path) -> None:
    client = _authenticated_client(tmp_path)
    response = client.get(
        "/api/config",
        headers={
            "Authorization": (
                "Bearer internal-token-with-at-least-thirty-two-characters"
            )
        },
    )

    assert response.status_code == 200


def test_login_rate_limit_returns_retry_after_without_echoing_password(
    tmp_path: Path,
) -> None:
    client = _authenticated_client(tmp_path)
    password = "incorrect-password-must-not-leak"
    responses = [
        client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
            headers={"Origin": "https://admin.example.com"},
        )
        for _ in range(6)
    ]

    assert [response.status_code for response in responses] == [
        401,
        401,
        401,
        401,
        401,
        429,
    ]
    assert int(responses[-1].headers["retry-after"]) > 0
    assert all(password not in response.text for response in responses)


def test_forced_password_change_blocks_console_until_completed(tmp_path: Path) -> None:
    client = _authenticated_client(tmp_path, force_change=True)
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct-password"},
        headers={"Origin": "https://admin.example.com"},
    )
    csrf = login.json()["csrf_token"]

    assert client.get("/api/config").status_code == 403
    changed = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "correct-password",
            "new_password": "new-correct-password",
        },
        headers={
            "Origin": "https://admin.example.com",
            "X-CSRF-Token": csrf,
        },
    )
    assert changed.status_code == 200
    assert client.get("/api/config").status_code == 401


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


def test_config_update_accepts_independent_subagent_limits() -> None:
    client = _client()
    current = client.get("/api/config").json()
    config = {
        **current["config"],
        "subagent_slots": [
            {
                "slot_id": "investigator",
                "enabled": True,
                "provider": "openai",
                "base_url": "https://api.openai.com",
                "model": "gpt-5.6-luna",
                "credential_ref": "secret/child",
                "context_window_tokens": 24_000,
                "max_tool_rounds": 8,
                "max_parallel": 2,
            }
        ],
    }

    response = client.put(
        "/api/config",
        json={"expected_revision": current["revision"], "config": config},
        headers={"X-Admin-Actor": "operator@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["config"]["subagent_slots"] == [
        {
            "slot_id": "investigator",
            "enabled": True,
            "provider": "openai",
            "base_url": "https://api.openai.com",
            "model": "gpt-5.6-luna",
            "credential_ref": "secret/child",
            "context_window_tokens": 24_000,
            "max_tool_rounds": 8,
            "max_parallel": 2,
        }
    ]


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


def test_admin_image_falls_back_to_spa_entry_for_history_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>Casework console</main>", encoding="utf-8")
    monkeypatch.setenv("ADMIN_WEB_DIST", str(dist))

    client = _client()

    response = client.get("/harness")

    assert response.status_code == 200
    assert "Casework console" in response.text


def test_admin_image_does_not_turn_unknown_api_requests_into_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>Casework console</main>", encoding="utf-8")
    monkeypatch.setenv("ADMIN_WEB_DIST", str(dist))

    client = _client()

    response = client.get("/api/unknown")

    assert response.status_code == 404


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
        "error_code": "unknown_tool",
        "detail": "Tool is not registered",
    }


class _Probe:
    async def probe(self, tool_id: str) -> ConnectionProbeResponse:
        return ConnectionProbeResponse(
            tool_id=tool_id,
            status="connected",
            latency_ms=12,
        )


def test_registry_connection_test_uses_injected_typed_probe() -> None:
    reads = InMemoryAdminReadRepository(
        registry=RegistryResponse(
            tools=(
                ToolRecord(
                    id="sql",
                    name="SQL",
                    description="read-only",
                    scope="root only",
                    contract="typed",
                    connection="untested",
                    latency="—",
                ),
            )
        ),
        tool_probe=_Probe(),
    )
    client = cast(
        _Client,
        TestClient(
            create_app(
                repository=InMemoryConfigRepository(initial=_config()),
                read_repository=reads,
            )
        ),
    )

    response = client.post("/api/registry/tools/sql/test")

    assert response.status_code == 200
    assert response.json() == {
        "tool_id": "sql",
        "status": "connected",
        "latency_ms": 12,
        "error_code": None,
        "detail": None,
    }


def test_config_validation_does_not_echo_rejected_credential_material() -> None:
    client = _client()
    current = client.get("/api/config").json()
    secret = "sk-live-should-never-be-returned"

    response = client.put(
        "/api/config",
        json={
            "expected_revision": current["revision"],
            "config": {
                **current["config"],
                "root_model": {
                    **current["config"]["root_model"],
                    "credential_ref": secret,
                },
            },
        },
        headers={"X-Admin-Actor": "operator@example.com"},
    )

    assert response.status_code == 422
    assert secret not in response.text


def test_model_credential_status_is_masked_and_successful_replace_is_atomic(
    tmp_path: Path,
) -> None:
    credential_file = tmp_path / "credentials.env"
    credential_file.write_text(
        'CASEWORK_CREDENTIAL_SECRET_ROOT="previous-secret"\n', encoding="utf-8"
    )
    probe = _ModelProbe(ModelProbeResult(status="connected", latency_ms=17))
    model_credentials = ModelCredentialService(
        store=FileCredentialStore(credential_file), probe=probe
    )
    client = cast(
        _Client,
        TestClient(
            create_app(
                repository=InMemoryConfigRepository(initial=_config()),
                model_credentials=model_credentials,
            )
        ),
    )

    before = client.get("/api/model-credentials")
    replaced = client.put(
        "/api/model-credentials",
        json={"credential_ref": "secret/root", "secret": "replacement-secret"},
        headers={"X-Admin-Actor": "operator@example.com"},
    )

    assert before.status_code == 200
    assert before.json()["items"][0]["masked_value"].endswith("cret")
    assert "previous-secret" not in before.text
    assert replaced.status_code == 200
    assert replaced.json()["probe"] == {
        "status": "connected",
        "latency_ms": 17,
        "error_code": None,
    }
    assert "replacement-secret" not in replaced.text
    assert (
        parse_credential_file(credential_file)["CASEWORK_CREDENTIAL_SECRET_ROOT"]
        == "replacement-secret"
    )


def test_model_catalog_uses_candidate_without_echoing_or_persisting_it(
    tmp_path: Path,
) -> None:
    credential_file = tmp_path / "credentials.env"
    probe = _ModelProbe(ModelProbeResult(status="connected"))
    service = ModelCredentialService(
        store=FileCredentialStore(credential_file), probe=probe
    )
    client = cast(
        _Client,
        TestClient(
            create_app(
                repository=InMemoryConfigRepository(initial=_config()),
                model_credentials=service,
            )
        ),
    )
    candidate = "catalog-candidate-secret"

    response = client.post(
        "/api/model-catalog",
        json={
            "provider": "openai",
            "base_url": "https://models.example.com/v1",
            "secret": candidate,
        },
    )

    assert response.status_code == 200
    assert response.json()["models"] == ["gpt-a", "gpt-b"]
    assert candidate not in response.text
    assert not credential_file.exists()


def test_failed_model_credential_probe_does_not_replace_or_echo_candidate(
    tmp_path: Path,
) -> None:
    credential_file = tmp_path / "credentials.env"
    credential_file.write_text(
        'CASEWORK_CREDENTIAL_SECRET_ROOT="previous-secret"\n', encoding="utf-8"
    )
    model_credentials = ModelCredentialService(
        store=FileCredentialStore(credential_file),
        probe=_ModelProbe(
            ModelProbeResult(status="degraded", error_code="HTTP_401_invalid_api_key")
        ),
    )
    client = cast(
        _Client,
        TestClient(
            create_app(
                repository=InMemoryConfigRepository(initial=_config()),
                model_credentials=model_credentials,
            )
        ),
    )
    candidate = "invalid-candidate-secret"

    response = client.put(
        "/api/model-credentials",
        json={"credential_ref": "secret/root", "secret": candidate},
        headers={"X-Admin-Actor": "operator@example.com"},
    )

    assert response.status_code == 422
    assert response.json()["detail"].endswith("HTTP_401_invalid_api_key")
    assert candidate not in response.text
    assert (
        parse_credential_file(credential_file)["CASEWORK_CREDENTIAL_SECRET_ROOT"]
        == "previous-secret"
    )


def test_admin_api_keeps_unpersisted_run_activity_as_json_null() -> None:
    reads = InMemoryAdminReadRepository(
        runs=(
            RunRecord(
                ticket_id=3726,
                subject="Ticket #3726",
                status="completed",
                status_label="已完成",
                started_at="2026-09-01T00:00:00Z",
                duration="1m 0s",
                session_id="session-3726",
                sandbox="destroyed",
                outcome="completed",
            ),
        )
    )
    client = cast(
        _Client,
        TestClient(
            create_app(
                repository=InMemoryConfigRepository(initial=_config()),
                read_repository=reads,
            )
        ),
    )

    run = client.get("/api/runs").json()["items"][0]

    assert run["question"] is None
    assert run["steps"] is None
    assert run["todo_completed"] is None
    assert run["todo_total"] is None
    assert run["tool_calls"] is None
    assert run["evidence"] is None
    assert run["checkpoint"] is None
