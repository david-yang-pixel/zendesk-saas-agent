"""Test feishu command parser."""
from zd_agent.integrations.feishu.commands import Command, parse_command


def test_parse_branch_command():
    cmd = parse_command("/branch dify hotfix/1.11.2-fix3")
    assert cmd is not None
    assert cmd.name == "branch"
    assert cmd.args == ["dify", "hotfix/1.11.2-fix3"]
    assert cmd.raw == "/branch dify hotfix/1.11.2-fix3"


def test_parse_reprocess_command():
    cmd = parse_command("/reprocess 12345")
    assert cmd.name == "reprocess"
    assert cmd.args == ["12345"]


def test_parse_status_with_ticket():
    cmd = parse_command("/status 12345")
    assert cmd.name == "status"
    assert cmd.args == ["12345"]


def test_parse_status_no_args():
    cmd = parse_command("/status")
    assert cmd.name == "status"
    assert cmd.args == []


def test_parse_fetch_all():
    cmd = parse_command("/fetch-all")
    assert cmd.name == "fetch-all"
    assert cmd.args == []


def test_parse_unknown():
    cmd = parse_command("/xyz abc")
    assert cmd.name == "unknown"
    assert cmd.args == ["xyz", "abc"]


def test_parse_non_command():
    assert parse_command("hello, not a command") is None
    assert parse_command("") is None


def test_parse_strips_whitespace():
    cmd = parse_command("  /pause  ")
    assert cmd.name == "pause"


def test_parse_lowercases_name():
    cmd = parse_command("/BRANCH dify main")
    assert cmd.name == "branch"


def test_parse_empty_after_slash():
    assert parse_command("/") is None
    assert parse_command("/ ") is None


def test_parse_help():
    cmd = parse_command("/help")
    assert cmd.name == "help"
