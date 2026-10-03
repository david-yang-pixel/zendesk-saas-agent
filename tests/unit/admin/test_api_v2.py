import json
from contextlib import asynccontextmanager

import httpx
import pytest
from starlette.testclient import TestClient

import casework_admin.api_v2 as api_v2
from casework_admin.api_v2 import _McpProbeError, _probe_mcp_tools, register_modern_routes
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
