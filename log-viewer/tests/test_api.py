import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def _setup(tmp_path, monkeypatch):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [
        {"event": "trigger_verdict", "ticket_id": 42, "verdict": "substantial_issue",
         "timestamp": "2026-05-29T04:00:00Z"},
        {"event": "agent_done", "ticket_id": 42, "rounds": 7, "total_tokens": 370297,
         "status": "completed", "reply_sent": True, "timestamp": "2026-05-29T04:02:00Z"},
    ]
    (tmp_path / f"events-{date}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )
    monkeypatch.setenv("ZD_LOG_DIR", str(tmp_path))
    import importlib
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_api_tickets_list(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/api/tickets")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["ticket_id"] == 42
    assert data[0]["rounds"] == 7


def test_api_ticket_detail(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/api/tickets/42")
    assert r.status_code == 200
    tl = r.json()
    assert [e["event"] for e in tl] == ["trigger_verdict", "agent_done"]


def test_api_ticket_404(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/api/tickets/99999")
    assert r.status_code == 404


def test_api_index_served(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "工单" in r.text
