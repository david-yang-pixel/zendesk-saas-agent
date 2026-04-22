"""Test silence manager (connection-burst detection)."""
from datetime import datetime, timedelta, timezone

import pytest

from zd_agent.trigger.silence import compute_silence_duration


class MockComment:
    def __init__(self, iso_ts: str):
        self.created_at = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))


@pytest.fixture
def params():
    return {
        "default_min": 3,
        "extend_min": 5,
        "burst_window_s": 30,
        "burst_threshold": 2,
    }


def test_empty_list_returns_default(params):
    assert compute_silence_duration([], **params) == timedelta(minutes=3)


def test_single_message_returns_default(params):
    comments = [MockComment("2026-04-22T10:00:00Z")]
    assert compute_silence_duration(comments, **params) == timedelta(minutes=3)


def test_rapid_burst_extends_to_5min(params):
    comments = [
        MockComment("2026-04-22T10:00:00Z"),
        MockComment("2026-04-22T10:00:20Z"),
    ]
    assert compute_silence_duration(comments, **params) == timedelta(minutes=5)


def test_exactly_at_burst_window_edge_extends(params):
    comments = [
        MockComment("2026-04-22T10:00:00Z"),
        MockComment("2026-04-22T10:00:30Z"),
    ]
    assert compute_silence_duration(comments, **params) == timedelta(minutes=5)


def test_messages_spread_out_not_extended(params):
    comments = [
        MockComment("2026-04-22T10:00:00Z"),
        MockComment("2026-04-22T10:02:00Z"),
    ]
    assert compute_silence_duration(comments, **params) == timedelta(minutes=3)


def test_three_messages_within_window_extends(params):
    comments = [
        MockComment("2026-04-22T10:00:00Z"),
        MockComment("2026-04-22T10:00:10Z"),
        MockComment("2026-04-22T10:00:25Z"),
    ]
    assert compute_silence_duration(comments, **params) == timedelta(minutes=5)


def test_only_latest_two_count_for_burst(params):
    # 3 messages but first is way in the past; only last 2 within window
    comments = [
        MockComment("2026-04-22T09:00:00Z"),
        MockComment("2026-04-22T10:00:00Z"),
        MockComment("2026-04-22T10:00:15Z"),
    ]
    assert compute_silence_duration(comments, **params) == timedelta(minutes=5)


def test_higher_threshold_not_met(params):
    params = {**params, "burst_threshold": 3}
    comments = [
        MockComment("2026-04-22T10:00:00Z"),
        MockComment("2026-04-22T10:00:15Z"),
    ]
    # only 2 in burst, threshold is 3
    assert compute_silence_duration(comments, **params) == timedelta(minutes=3)


def test_comments_unsorted_still_work(params):
    comments = [
        MockComment("2026-04-22T10:00:20Z"),
        MockComment("2026-04-22T10:00:00Z"),
    ]
    assert compute_silence_duration(comments, **params) == timedelta(minutes=5)
