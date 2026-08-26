from __future__ import annotations

import json
import os
import signal
from unittest.mock import MagicMock

import pytest

from zd_agent.services.child_process_registry import (
    ChildProcessGroupRegistry,
    ChildProcessGroupRegistryError,
)


def test_registry_persists_process_groups_and_terminates_only_registered_groups(
    tmp_path, monkeypatch
) -> None:
    registry = ChildProcessGroupRegistry(tmp_path / "child-groups.json")
    registry.add(4101)
    registry.add(4102)
    registry.remove(4101)
    killpg = MagicMock()
    monkeypatch.setattr(os, "getpgrp", lambda: 9999)
    monkeypatch.setattr(os, "killpg", killpg)

    terminated = registry.terminate_all()

    assert terminated == 1
    killpg.assert_called_once_with(4102, signal.SIGKILL)
    assert json.loads(registry.path.read_text(encoding="utf-8")) == []


def test_corrupt_registry_fails_closed_instead_of_forgetting_process_groups(
    tmp_path,
) -> None:
    registry = ChildProcessGroupRegistry(tmp_path / "child-groups.json")
    registry.path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ChildProcessGroupRegistryError, match="JSONDecodeError"):
        registry.terminate_all()


def test_registry_never_terminates_control_plane_process_group(
    tmp_path, monkeypatch
) -> None:
    registry = ChildProcessGroupRegistry(tmp_path / "child-groups.json")
    registry.add(4102)
    killpg = MagicMock()
    monkeypatch.setattr(os, "getpgrp", lambda: 4102)
    monkeypatch.setattr(os, "killpg", killpg)

    terminated = registry.terminate_all()

    assert terminated == 0
    killpg.assert_not_called()
    assert json.loads(registry.path.read_text(encoding="utf-8")) == []
