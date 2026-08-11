import json
from datetime import datetime, timezone

from zd_agent.log import _make_jsonl_sink, get_logger, setup_logging


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


def test_setup_logging_with_jsonl_writes_file(tmp_path):
    setup_logging(json_output=False, log_level="INFO", jsonl_dir=str(tmp_path))
    log = get_logger(module="test")
    log.info("agent_done", ticket_id=999, rounds=5)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / f"events-{date}.jsonl"
    assert f.exists()
    found = [
        json.loads(line)
        for line in f.read_text().strip().splitlines()
        if json.loads(line).get("ticket_id") == 999
    ]
    assert found and found[0]["event"] == "agent_done"


def test_setup_logging_jsonl_dir_none_no_file(tmp_path):
    # jsonl_dir=None → 不写文件（向后兼容）
    setup_logging(json_output=False, log_level="INFO", jsonl_dir=None)
    log = get_logger(module="test")
    log.info("x", ticket_id=1)
    assert not list(tmp_path.glob("events-*.jsonl"))
