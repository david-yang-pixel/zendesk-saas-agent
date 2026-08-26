from unittest.mock import MagicMock, patch

import typer
from click import Group
from typer.testing import CliRunner

from zd_agent.config import AppConfig
from zd_agent.entry import cli


def test_runtime_command_applies_saved_config_before_logging_setup() -> None:
    cfg = AppConfig()
    events: list[str] = []

    def apply_saved(received_cfg):
        assert received_cfg is cfg
        events.append("config")

    def setup_logging(**_kwargs):
        events.append("logging")

    with (
        patch.object(cli, "get_config", return_value=cfg),
        patch(
            "zd_agent.services.app_bootstrap.apply_saved_admin_config",
            side_effect=apply_saved,
        ),
        patch.object(cli, "setup_logging", side_effect=setup_logging),
        patch(
            "zd_agent.services.control_plane_app.run_control_plane_app",
            side_effect=lambda received_cfg: events.append("control")
            if received_cfg is cfg
            else None,
        ),
    ):
        result = CliRunner().invoke(cli.app, ["serve"])

    assert result.exit_code == 0
    assert events == ["config", "logging", "control"]


def test_subcommand_help_does_not_initialize_or_write_runtime_state() -> None:
    cfg = AppConfig()

    with (
        patch("zd_agent.config.get_config", return_value=cfg),
        patch(
            "zd_agent.services.app_bootstrap.apply_saved_admin_config"
        ) as apply_saved,
        patch.object(cli, "setup_logging") as setup,
    ):
        result = CliRunner().invoke(cli.app, ["run", "--help"])

    assert result.exit_code == 0
    assert "工单 ID" in result.output
    apply_saved.assert_not_called()
    setup.assert_not_called()


def test_serve_starts_control_plane_without_creating_worker_in_process() -> None:
    cfg = AppConfig()
    run_app = MagicMock()

    with (
        patch.object(cli, "get_config", return_value=cfg),
        patch("zd_agent.services.control_plane_app.run_control_plane_app", run_app),
    ):
        cli.serve()

    run_app.assert_called_once_with(cfg)


def test_serve_dry_run_forces_all_webhook_sessions_to_no_write() -> None:
    cfg = AppConfig()

    with (
        patch.object(cli, "get_config", return_value=cfg),
        patch.dict("os.environ", {}, clear=False),
        patch("zd_agent.services.control_plane_app.run_control_plane_app") as run_app,
    ):
        cli.serve(dry_run=True)
        assert cli.os.environ["ZD_AGENT_FORCE_DRY_RUN"] == "1"

    run_app.assert_called_once_with(cfg)


def test_top_level_help_exposes_only_the_durable_http_server_command() -> None:
    command = typer.main.get_command(cli.app)
    assert isinstance(command, Group)
    assert {"run", "batch", "serve"} <= set(command.commands)
    assert "webhook" not in command.commands

    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "serve" in result.output
