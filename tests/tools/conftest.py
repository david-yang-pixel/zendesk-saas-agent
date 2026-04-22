"""Shared fixtures for tool tests."""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_session(tmp_path):
    """Minimal AgentSession stub for tool tests."""
    session = MagicMock()
    session.ticket_id = 12345
    session.waiting_for_auth = False

    def _mark():
        session.waiting_for_auth = True
    session.mark_waiting_for_auth = _mark
    return session
