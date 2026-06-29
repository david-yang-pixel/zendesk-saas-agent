from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_github_mcp_exposes_only_github_tools(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_MCP_API_KEY", "github-secret")
    from tool_services.settings import get_settings

    get_settings.cache_clear()
    from tool_services.app import create_service_app

    client = TestClient(create_service_app("github-mcp"))
    response = client.post(
        "/mcp",
        headers=_auth("github-secret"),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    assert response.status_code == 200
    tool_names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert tool_names == {"github_search_prs", "github_get_pr", "github_code_search"}


def test_docs_mcp_rejects_github_tool(monkeypatch) -> None:
    monkeypatch.setenv("DOCS_TOOL_SERVICE_API_KEY", "docs-secret")
    from tool_services.settings import get_settings

    get_settings.cache_clear()
    from tool_services.app import create_service_app

    client = TestClient(create_service_app("docs-tool-service"))
    response = client.post(
        "/mcp",
        headers=_auth("docs-secret"),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "github_search_prs", "arguments": {}},
        },
    )

    assert response.status_code == 200
    assert response.json()["error"]["message"] == "unknown tool: github_search_prs"


def test_notification_service_requires_own_api_key(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFICATION_SERVICE_API_KEY", "notify-secret")
    from tool_services.settings import get_settings

    get_settings.cache_clear()
    from tool_services.app import create_service_app

    client = TestClient(create_service_app("notification-service"))
    response = client.post(
        "/mcp",
        headers=_auth("wrong-secret"),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    assert response.status_code == 401


def test_sandbox_worker_rejects_paths_outside_base(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SANDBOX_WORKER_API_KEY", "sandbox-secret")
    monkeypatch.setenv("SANDBOX_WORKER_BASE_DIR", str(tmp_path / "sandbox"))
    from tool_services.settings import get_settings

    get_settings.cache_clear()
    from tool_services.app import create_service_app

    client = TestClient(create_service_app("sandbox-worker"))
    response = client.post(
        "/mcp",
        headers=_auth("sandbox-secret"),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "sandbox_read",
                "arguments": {
                    "sandbox_dir": str(tmp_path / "outside"),
                    "filepath": "x.txt",
                },
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]
    assert payload["success"] is False
    assert payload["code"] == "BAD_ARGUMENT"
