import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

import casework_admin.api_v2 as api_v2
from casework_admin.api_v2 import _McpProbeError, _probe_mcp_tools, _run_reply, register_modern_routes
from casework_admin.app import create_app


@asynccontextmanager
async def dummy_scope():
    yield None


def test_modern_skills_route():
    app = create_app()
    register_modern_routes(app, dummy_scope)
    client = TestClient(app)

    res = client.get("/api/skills")
    assert res.status_code == 200
    skills = res.json()
    assert isinstance(skills, list)
    assert any(s["name"] == "core-casework" for s in skills)


def test_modern_model_settings_route(monkeypatch: pytest.MonkeyPatch) -> None:
    # The model page is the only source of model settings; the environment
    # must not supply a fallback model, endpoint or key.
    monkeypatch.setenv("CASEWORK_MODEL_NAME", "env-model")
    monkeypatch.setenv("CASEWORK_MODEL_BASE_URL", "https://env.example.test/v1")
    monkeypatch.setenv("CASEWORK_CREDENTIAL_OPENAI_PRIMARY", "env-secret-key")
    app = create_app()
    register_modern_routes(app, dummy_scope)
    client = TestClient(app)

    res = client.get("/api/settings/model")
    assert res.status_code == 200
    data = res.json()
    assert data["provider"] == "openai"
    assert data["model"] == ""
    assert data["base_url"] == ""
    assert data["has_key"] is False

    health = client.get("/api/system/mesh-health").json()
    llm = next(item for item in health["items"] if item["name"] == "llm")
    assert llm["status"] == "error"
    assert "env-model" not in llm["label"]


def _run(outcome: dict, records: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(outcome=outcome, tool_call_records=records or [])


def test_run_reply_shows_the_operator_draft_this_run_delivered() -> None:
    internal = {"final_reply": "", "delivery": {"channel": "zendesk-internal-note", "status": "sent"}}

    assert _run_reply(_run({**internal, "internal_draft": "We are checking the credits."}), "Older plugin reply") == (
        "We are checking the credits.",
        "internal_draft",
    )
    # Runs recorded before internal_draft existed: read the reviewed answer step,
    # never the ticket's older outbox message.
    legacy = _run(internal, [
        {"type": "answer", "text": "=== INTERNAL NOTE ===\nfirst\n=== CUSTOMER REPLY ===\nFirst draft"},
        {"type": "tool_call", "toolName": "read"},
        {"type": "answer", "text": "=== INTERNAL NOTE ===\nreviewed\n=== CUSTOMER REPLY ===\nReviewed draft"},
    ])
    assert _run_reply(legacy, "Older plugin reply") == ("Reviewed draft", "internal_draft")
    assert _run_reply(_run(internal), "Older plugin reply") == ("", "internal_draft")


def test_run_reply_uses_the_public_reply_or_this_runs_outbox_message() -> None:
    assert _run_reply(_run({"final_reply": "Sent reply"}), "Outbox body") == ("Sent reply", "public")
    assert _run_reply(_run({"final_reply": ""}), "Outbox body") == ("Outbox body", "public")
    assert _run_reply(None) == ("", "public")


def test_model_ping_uses_the_agent_responses_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.example.test/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-6-sol"
        assert payload["input"]
        assert payload["max_output_tokens"] >= 128
        assert "messages" not in payload
        assert "max_tokens" not in payload
        return httpx.Response(
            200,
            json={
                "output": [
                    {"type": "reasoning", "summary": []},
                    {"type": "message", "content": [{"type": "output_text", "text": "pong"}]},
                ]
            },
        )

    monkeypatch.setattr(
        api_v2.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    app = create_app()
    register_modern_routes(app, dummy_scope)

    response = TestClient(app).post(
        "/api/settings/model/test",
        json={"base_url": "https://api.example.test/v1", "model": "gpt-6-sol", "api_key": "test-key"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["sample_text"] == "pong"


def test_model_ping_does_not_echo_upstream_error_details(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.AsyncClient

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "secret upstream details"}})

    monkeypatch.setattr(
        api_v2.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    app = create_app()
    register_modern_routes(app, dummy_scope)

    response = TestClient(app).post(
        "/api/settings/model/test",
        json={"base_url": "https://api.example.test/v1", "model": "gpt-6-sol", "api_key": "test-key"},
    )

    assert response.status_code == 200
    assert response.json()["status_code"] == 400
    assert "secret upstream details" not in response.text


def test_reasoning_level_detection_keeps_the_efforts_the_model_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.AsyncClient
    sent: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.example.test/v1/responses"
        payload = json.loads(request.content)
        sent.append(payload["reasoning"])
        effort = payload["reasoning"]["effort"]
        if effort in ("none", "minimal"):
            return httpx.Response(400, json={"error": {"message": "Unsupported value"}})
        # A gateway that rewrites an effort it does not support does not honour it.
        echoed = "high" if effort == "xhigh" else effort
        return httpx.Response(200, json={"status": "incomplete", "reasoning": {"effort": echoed}})

    monkeypatch.setattr(
        api_v2.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    app = create_app()
    register_modern_routes(app, dummy_scope)

    response = TestClient(app).post(
        "/api/settings/model/reasoning-levels",
        json={"base_url": "https://api.example.test", "model": "gpt-6-sol", "api_key": "test-key"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["levels"] == ["low", "medium", "high"]
    # Probes mirror what Pi Agent sends: "off" as effort none without a summary.
    assert {"effort": "none"} in sent
    assert {"effort": "medium", "summary": "auto"} in sent


def test_reasoning_level_detection_reports_an_unreachable_model(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.AsyncClient

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "secret upstream details"}})

    monkeypatch.setattr(
        api_v2.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    app = create_app()
    register_modern_routes(app, dummy_scope)

    response = TestClient(app).post(
        "/api/settings/model/reasoning-levels",
        json={"base_url": "https://api.example.test/v1", "model": "gpt-6-sol", "api_key": "test-key"},
    )

    assert response.json()["ok"] is False
    assert response.json()["levels"] == []
    assert "HTTP 401" in response.json()["message"]
    assert "secret upstream details" not in response.text


@pytest.mark.parametrize(
    ("levels", "effort"),
    [
        (None, None),
        (["low", "high"], None),
        (["low", "high"], "medium"),
        ([], "medium"),
        (["turbo"], "turbo"),
    ],
)
def test_model_settings_require_a_detected_reasoning_effort(levels: list[str] | None, effort: str | None) -> None:
    app = create_app()
    register_modern_routes(app, dummy_scope)

    response = TestClient(app).post(
        "/api/settings/model",
        json={
            "base_url": "https://api.example.test/v1",
            "model": "gpt-6-sol",
            "reasoning_levels": levels,
            "reasoning_effort": effort,
        },
    )

    assert response.status_code == 422


def test_mcp_mesh_reports_live_catalog_and_relay_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASEWORK_OPENCONNECTOR_MCP_URL", "https://connector.example/mcp")
    monkeypatch.setenv("CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME", "test-key")
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            assert request.headers["accept"] == "application/json, text/event-stream"
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": "mesh-probe", "result": {"tools": [{"name": "search_actions"}]}},
            )
        assert request.url.path == "/zendesk-browser-relay/health"
        return httpx.Response(429)

    monkeypatch.setattr(
        api_v2.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    app = create_app()
    register_modern_routes(app, dummy_scope)

    response = TestClient(app).get("/api/mcp/mesh")

    assert response.status_code == 200
    services = {service["id"]: service for service in response.json()["services"]}
    assert services["openconnector"]["online"] is True
    assert services["openconnector"]["tool_count"] == 1
    assert services["zendesk-browser-relay"]["online"] is False
    assert services["zendesk-browser-relay"]["tool_count"] == 0
    assert services["zendesk-browser-relay"]["error"] == "HTTP 429"


@pytest.mark.asyncio
async def test_mcp_probe_uses_agent_protocol_and_accepts_json() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json, text/event-stream"
        assert request.headers["mcp-protocol-version"] == "2026-07-28"
        assert request.headers["mcp-method"] == "tools/list"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mesh-probe", "result": {"tools": [{"name": "list_connections"}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        tools = await _probe_mcp_tools(client, "https://connector.example/mcp", "test-key")

    assert [tool["name"] for tool in tools] == ["list_connections"]


@pytest.mark.asyncio
async def test_mcp_probe_accepts_sse_and_ignores_intermediate_frames() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text='event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            'event: message\ndata: {"jsonrpc":"2.0","id":"mesh-probe","result":{"tools":[{"name":"search_actions"}]}}\n\n',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        tools = await _probe_mcp_tools(client, "https://connector.example/mcp", "test-key")

    assert [tool["name"] for tool in tools] == ["search_actions"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(406, text="secret upstream body"), "HTTP 406"),
        (httpx.Response(200, json={"error": {"message": "secret upstream body"}}), "MCP protocol error"),
        (httpx.Response(200, text=""), "Invalid MCP response"),
        (httpx.Response(200, json={"result": {"tools": "invalid"}}), "Invalid MCP tool list"),
    ],
)
async def test_mcp_probe_fails_closed_without_exposing_upstream_body(
    response: httpx.Response, message: str
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: response)) as client:
        with pytest.raises(_McpProbeError, match=message) as raised:
            await _probe_mcp_tools(client, "https://connector.example/mcp", "test-key")

    assert "secret upstream body" not in str(raised.value)
