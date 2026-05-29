import json
from datetime import datetime, timezone

from zd_agent.log import _make_jsonl_sink


def test_jsonl_sink_writes_line(tmp_path):
    sink = _make_jsonl_sink(str(tmp_path))
    event_dict = {"event": "agent_done", "ticket_id": 2709, "rounds": 7}
    out = sink(None, "info", event_dict)
    # processor 必须原样返回 event_dict（不消费）
    assert out == event_dict
    # 文件按当天 UTC 日期命名
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    assert f.exists()
    line = json.loads(f.read_text().strip())
    assert line["event"] == "agent_done"
    assert line["ticket_id"] == 2709


def test_jsonl_sink_appends(tmp_path):
    sink = _make_jsonl_sink(str(tmp_path))
    sink(None, "info", {"event": "a", "ticket_id": 1})
    sink(None, "info", {"event": "b", "ticket_id": 2})
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = (tmp_path / f"events-{date}.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "a"
    assert json.loads(lines[1])["event"] == "b"


def test_jsonl_sink_swallows_write_error(tmp_path):
    # 不可写目录 → 不能抛异常（降级），仍返回 event_dict
    sink = _make_jsonl_sink("/nonexistent/cannot/create/here")
    out = sink(None, "info", {"event": "x", "ticket_id": 1})
    assert out == {"event": "x", "ticket_id": 1}
