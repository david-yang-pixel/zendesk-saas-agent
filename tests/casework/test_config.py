from pathlib import Path

import yaml

from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.config import AppConfig, load_config


def test_casework_defaults_to_legacy_and_cannot_change_production_behavior() -> None:
    config = AppConfig()

    assert config.casework.mode is ExecutionMode.LEGACY
    assert config.casework.shadow_archive_dir == "casework-shadow"


def test_shadow_mode_and_archive_path_load_explicitly(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "resources:\n"
        f"  base_dir: {tmp_path / 'resources'}\n"
        "  state_dir: .state\n"
        "casework:\n"
        "  mode: shadow\n"
        "  shadow_archive_dir: decisions\n",
    )

    config = load_config(str(config_path))

    assert config.casework.mode is ExecutionMode.SHADOW
    assert config.casework.shadow_archive_dir == str(
        tmp_path / "resources" / ".state" / "decisions",
    )


def test_casework_mode_can_be_enabled_by_explicit_environment_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("casework:\n  mode: legacy\n")
    monkeypatch.setenv("ZD_CASEWORK_MODE", "shadow")

    config = load_config(str(config_path))

    assert config.casework.mode is ExecutionMode.SHADOW


def test_repository_config_keeps_casework_on_legacy_path() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text())

    assert raw["casework"]["mode"] == "legacy"
