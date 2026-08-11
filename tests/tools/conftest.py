"""Shared fixtures for tool tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_session(tmp_path):
    """Minimal AgentSession stub for tool tests."""
    session = MagicMock()
    session.ticket_id = 12345
    return session
