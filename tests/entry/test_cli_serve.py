from unittest.mock import MagicMock, patch

import typer
from click import Group
from typer.testing import CliRunner

from zd_agent.config import AppConfig
from zd_agent.entry import cli


def test_runtime_command_applies_saved_config_before_logging_setup() -> None:
    cfg = AppConfig()
    events: list[str] = []
    manager = MagicMock()

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
            "zd_agent.services.app_bootstrap.create_configured_session_manager",
            side_effect=lambda _cfg, **_kwargs: events.append("manager") or manager,
        ),
        patch(
            "zd_agent.services.saas_app.run_saas_app",
            side_effect=lambda _manager: events.append("run"),
        ),
    ):
        result = CliRunner().invoke(cli.app, ["serve"])

    assert result.exit_code == 0
    assert events == ["config", "logging", "manager", "run"]


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


def test_serve_applies_saved_config_before_session_manager() -> None:
    cfg = AppConfig()
    events: list[str] = []

    manager = MagicMock()

    def create_manager(received_cfg, *, force_dry_run=False):
        assert received_cfg is cfg
        assert force_dry_run is False
        events.append("manager")
        return manager

    configured_manager = MagicMock(side_effect=create_manager)
    run_app = MagicMock(side_effect=lambda _manager: events.append("run"))

    with (
        patch.object(cli, "get_config", return_value=cfg),
        patch(
            "zd_agent.services.app_bootstrap.create_configured_session_manager",
            configured_manager,
        ),
        patch("zd_agent.services.saas_app.run_saas_app", run_app),
    ):
        cli.serve()

    assert events == ["manager", "run"]
    run_app.assert_called_once_with(manager)


def test_serve_dry_run_forces_all_webhook_sessions_to_no_write() -> None:
    cfg = AppConfig()
    manager = MagicMock()

    with (
        patch.object(cli, "get_config", return_value=cfg),
        patch(
            "zd_agent.services.app_bootstrap.create_configured_session_manager",
            return_value=manager,
        ) as create_manager,
        patch("zd_agent.services.saas_app.run_saas_app") as run_app,
    ):
        cli.serve(dry_run=True)

    create_manager.assert_called_once_with(cfg, force_dry_run=True)
    run_app.assert_called_once_with(manager)


def test_top_level_help_exposes_only_the_durable_http_server_command() -> None:
    command = typer.main.get_command(cli.app)
    assert isinstance(command, Group)
    assert {"run", "batch", "serve"} <= set(command.commands)
    assert "webhook" not in command.commands

    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "serve" in result.output
