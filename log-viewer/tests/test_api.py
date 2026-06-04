import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def _setup(tmp_path, monkeypatch, records_dir=None):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [
        {"event": "trigger_verdict", "ticket_id": 42, "verdict": "substantial_issue",
         "reason": "客户提供了明确问题。", "timestamp": "2026-05-29T04:00:00Z"},
        {"event": "ticket_classified", "ticket_id": 42, "ticket_type": "knowledge_base",
         "reason": "知识库已有对应说明。", "timestamp": "2026-05-29T04:00:10Z"},
        {"event": "round_tool_calls", "ticket_id": 42, "round": 1,
         "tool_call_count": 1, "tool_names": ["kb_search"],
         "timestamp": "2026-05-29T04:01:00Z"},
        {"event": "agent_done", "ticket_id": 42, "rounds": 7, "total_tokens": 370297,
         "status": "completed", "reply_sent": True, "timestamp": "2026-05-29T04:02:00Z"},
    ]
    (tmp_path / f"events-{date}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )
    monkeypatch.setenv("ZD_LOG_DIR", str(tmp_path))
    if records_dir is not None:
        monkeypatch.setenv("ZD_RECORDS_DIR", str(records_dir))
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
    assert [e["event"] for e in tl] == [
        "trigger_verdict",
        "ticket_classified",
        "round_tool_calls",
        "agent_done",
    ]


def test_api_ticket_404(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/api/tickets/99999")
    assert r.status_code == 404


def test_api_ticket_review_with_archive(tmp_path, monkeypatch):
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    (records_dir / "42.md").write_text(
        "---\nstatus: solved\n---\n\n"
        "## Internal Analysis\n\n客户命中已知知识库问题。\n\n"
        "## 客户回复草稿\n\n您好，请参考这篇文档。\n",
        encoding="utf-8",
    )
    client = _setup(tmp_path, monkeypatch, records_dir=records_dir)

    r = client.get("/api/tickets/42/review")

    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["ticket_id"] == 42
    assert data["summary"]["status"] == "completed"
    assert data["archive"]["available"] is True
    assert data["archive"]["sections"][0]["category"] == "internal_analysis"
    assert data["highlights"]["trigger_verdict"]["reason"] == "客户提供了明确问题。"
    assert data["highlights"]["classification"]["ticket_type"] == "knowledge_base"
    assert data["highlights"]["tool_rounds"][0]["tool_names"] == ["kb_search"]


def test_api_ticket_review_without_archive(tmp_path, monkeypatch):
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    client = _setup(tmp_path, monkeypatch, records_dir=records_dir)

    r = client.get("/api/tickets/42/review")

    assert r.status_code == 200
    data = r.json()
    assert data["archive"]["available"] is False
    assert data["archive"]["sections"] == []


def test_api_index_served(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "工单" in r.text
