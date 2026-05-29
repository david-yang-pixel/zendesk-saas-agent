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
