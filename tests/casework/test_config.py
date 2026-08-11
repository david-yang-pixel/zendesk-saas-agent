from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.config import AppConfig, CaseworkConfig, load_config


def test_casework_defaults_to_v2_production_behavior() -> None:
    config = AppConfig()

    assert config.casework.mode is ExecutionMode.CASEWORK
    assert config.casework.canary_ticket_ids == ()


def test_saas_intake_defaults_start_immediately() -> None:
    config = AppConfig()

    assert config.public_reply.silent_initial_wait_seconds == 0
    assert config.public_reply.silence_window_seconds == 0
    assert config.silence_waker.scan_interval_seconds == 1


@pytest.mark.parametrize("mode", ["legacy", "shadow"])
def test_removed_casework_modes_fail_fast_from_yaml(
    mode: str,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"casework:\n  mode: {mode}\n",
    )

    with pytest.raises(
        ValidationError,
        match=rf"casework mode '{mode}' is no longer supported",
    ):
        load_config(str(config_path))


@pytest.mark.parametrize("mode", ["legacy", "shadow"])
def test_removed_casework_modes_fail_fast_from_environment(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("casework:\n  mode: casework\n")
    monkeypatch.setenv("ZD_CASEWORK_MODE", mode)

    with pytest.raises(
        ValidationError,
        match=rf"casework mode '{mode}' is no longer supported",
    ):
        load_config(str(config_path))


def test_repository_config_uses_the_v2_casework_path() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text())

    assert raw["casework"]["mode"] == "casework"
    assert "shadow_archive_dir" not in raw["casework"]


def test_repository_config_starts_saas_intake_immediately() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text())

    assert raw["public_reply"]["silent_initial_wait_seconds"] == 0
    assert raw["public_reply"]["silence_window_seconds"] == 0
    assert raw["silence_waker"]["scan_interval_seconds"] == 1


@pytest.mark.parametrize("mode", [ExecutionMode.CANARY, ExecutionMode.CASEWORK])
def test_v2_delivery_modes_are_explicitly_configurable(mode: ExecutionMode) -> None:
    config = AppConfig(casework=CaseworkConfig(mode=mode))

    assert config.casework.mode is mode


def test_casework_environment_mode_loads_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("casework:\n  mode: canary\n")
    monkeypatch.setenv("ZD_CASEWORK_MODE", "casework")

    config = load_config(str(config_path))

    assert config.casework.mode is ExecutionMode.CASEWORK


def test_canary_ticket_ids_load_from_environment_without_widening_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("casework:\n  mode: casework\n")
    monkeypatch.setenv("ZD_CASEWORK_MODE", "canary")
    monkeypatch.setenv("ZD_CASEWORK_CANARY_TICKET_IDS", "3821, 3846,3821")

    config = load_config(str(config_path))

    assert config.casework.mode is ExecutionMode.CANARY
    assert config.casework.canary_ticket_ids == (3821, 3846)
