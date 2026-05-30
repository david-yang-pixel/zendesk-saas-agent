import json
from datetime import datetime, timezone

import parser as P


def _write_jsonl(tmp_path, rows):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


def test_read_events_basic(tmp_path):
    _write_jsonl(tmp_path, [
        {"event": "a", "ticket_id": 1},
        {"event": "b", "ticket_id": 2},
    ])
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) == 2
    assert {e["event"] for e in events} == {"a", "b"}


def test_read_events_skips_bad_lines(tmp_path):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    f.write_text('{"event":"ok","ticket_id":1}\nNOT JSON GARBAGE\n{"event":"ok2","ticket_id":2}\n')
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) == 2


def test_read_events_empty_dir(tmp_path):
    assert P.read_events(str(tmp_path), days=1) == []


def test_group_by_ticket():
    events = [
        {"event": "a", "ticket_id": 1},
        {"event": "b", "ticket_id": 2},
        {"event": "c", "ticket_id": 1},
        {"event": "startup"},
    ]
    grouped = P.group_by_ticket(events)
    assert set(grouped.keys()) == {1, 2}
    assert len(grouped[1]) == 2


def test_summarize_tickets():
    events = [
        {"event": "trigger_verdict", "ticket_id": 5, "verdict": "substantial_issue",
         "timestamp": "2026-05-29T04:00:00Z"},
        {"event": "ticket_classified", "ticket_id": 5, "ticket_type": "knowledge_base",
         "timestamp": "2026-05-29T04:00:05Z"},
        {"event": "agent_done", "ticket_id": 5, "rounds": 7, "total_tokens": 370297,
         "reply_sent": True, "status": "completed", "elapsed": 150.6,
         "timestamp": "2026-05-29T04:02:30Z"},
    ]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert len(summaries) == 1
    s = summaries[0]
    assert s["ticket_id"] == 5
    assert s["status"] == "completed"
    assert s["verdict"] == "substantial_issue"
    assert s["rounds"] == 7
    assert s["total_tokens"] == 370297
    assert s["reply_sent"] is True
    assert s["last_time"] == "2026-05-29T04:02:30Z"


def test_summarize_sorts_desc_by_last_time():
    events = [
        {"event": "agent_done", "ticket_id": 1, "timestamp": "2026-05-29T01:00:00Z"},
        {"event": "agent_done", "ticket_id": 2, "timestamp": "2026-05-29T03:00:00Z"},
        {"event": "agent_done", "ticket_id": 3, "timestamp": "2026-05-29T02:00:00Z"},
    ]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert [s["ticket_id"] for s in summaries] == [2, 3, 1]


def test_ticket_timeline_sorted_asc():
    events = [
        {"event": "agent_done", "ticket_id": 9, "timestamp": "2026-05-29T04:02:00Z"},
        {"event": "trigger_verdict", "ticket_id": 9, "timestamp": "2026-05-29T04:00:00Z"},
    ]
    tl = P.ticket_timeline(P.group_by_ticket(events), 9)
    assert [e["event"] for e in tl] == ["trigger_verdict", "agent_done"]


def test_summarize_missing_fields_no_crash():
    events = [{"event": "trigger_verdict", "ticket_id": 7, "timestamp": "2026-05-29T04:00:00Z"}]
    summaries = P.summarize_tickets(P.group_by_ticket(events))
    assert summaries[0]["ticket_id"] == 7
    assert summaries[0]["status"] == ""
    assert summaries[0]["rounds"] is None


import os as _os


def test_real_sample_parses(tmp_path):
    fixture = _os.path.join(_os.path.dirname(__file__), "fixtures", "sample.jsonl")
    from datetime import datetime as _d, timezone as _t
    date = _d.now(_t.utc).strftime("%Y-%m-%d")
    (tmp_path / f"events-{date}.jsonl").write_text(open(fixture, encoding="utf-8").read())
    events = P.read_events(str(tmp_path), days=1)
    assert len(events) > 0
    grouped = P.group_by_ticket(events)
    assert len(grouped) >= 1
    summaries = P.summarize_tickets(grouped)
    assert all("ticket_id" in s for s in summaries)
    # startup 行（无 ticket_id）必须被丢弃，不出现在任何工单分组里
    assert 2709 in grouped


def test_read_events_days_zero_reads_all_history():
    """days<=0 读取全部历史日志（不按日期过滤）。"""
    with tempfile.TemporaryDirectory() as d:
        today = datetime.now(timezone.utc).date().isoformat()
        _write(d, f"events-{today}.jsonl", [{"ticket_id": 1, "event": "x"}])
        _write(d, "events-2000-01-01.jsonl", [{"ticket_id": 2, "event": "y"}])
        ev = P.read_events(d, days=0)
        ids = sorted(e["ticket_id"] for e in ev)
        assert ids == [1, 2]
