import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from zd_agent.trigger.state_store import (
    TicketState,
    TicketStateCorruptedError,
    TicketStateTracker,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_invalid_state_json_shape_is_quarantined_and_reported(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    path.write_text("[]", encoding="utf-8")

    with (
        patch("zd_agent.trigger.state_store.logger.error") as log_error,
        pytest.raises(
            TicketStateCorruptedError,
        ),
    ):
        TicketStateTracker(path)

    assert not path.exists()
    assert len(list(tmp_path.glob("tickets.json.corrupt-*"))) == 1
    log_error.assert_called_once()


def test_invalid_persisted_state_is_quarantined_and_reported(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    path.write_text(
        json.dumps(
            {
                "1": {
                    "state": "unknown",
                    "first_seen": _now().isoformat(),
                },
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("zd_agent.trigger.state_store.logger.error") as log_error,
        pytest.raises(
            TicketStateCorruptedError,
        ),
    ):
        TicketStateTracker(path)

    assert not path.exists()
    assert len(list(tmp_path.glob("tickets.json.corrupt-*"))) == 1
    log_error.assert_called_once()


def test_setting_unchanged_state_skips_full_file_write(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    store = TicketStateTracker(path)
    state = TicketState(state="processed", first_seen=_now())
    store.set("1", state)

    with patch("zd_agent.trigger.state_store.atomic_write_text") as write:
        store.set("1", state)

    write.assert_not_called()


def test_deleting_missing_state_skips_full_file_write(tmp_path) -> None:
    store = TicketStateTracker(tmp_path / "tickets.json")

    with patch("zd_agent.trigger.state_store.atomic_write_text") as write:
        store.delete("missing")

    write.assert_not_called()


def test_failed_write_is_retried_by_the_next_set(tmp_path) -> None:
    path = tmp_path / "tickets.json"
    store = TicketStateTracker(path)
    state = TicketState(state="processed", first_seen=_now())
    store.set("1", state)
    state.ask_count = 1

    with (
        patch(
            "zd_agent.trigger.state_store.atomic_write_text",
            side_effect=OSError("disk unavailable"),
        ),
        pytest.raises(OSError, match="disk unavailable"),
    ):
        store.set("1", state)

    store.set("1", state)

    assert TicketStateTracker(path).get("1").ask_count == 1
