from unittest.mock import MagicMock, patch

from zd_agent.config import AppConfig
from zd_agent.entry import cli


def test_cli_main_applies_saved_config_before_logging_setup() -> None:
    cfg = AppConfig()
    events: list[str] = []

    def apply_saved(received_cfg):
        assert received_cfg is cfg
        events.append("config")

    def setup_logging(**_kwargs):
        events.append("logging")

    with (
        patch("zd_agent.config.get_config", return_value=cfg),
        patch(
            "zd_agent.services.app_bootstrap.apply_saved_admin_config",
            side_effect=apply_saved,
        ),
        patch.object(cli, "setup_logging", side_effect=setup_logging),
    ):
        cli.main(json_log=False)

    assert events == ["config", "logging"]


def test_serve_applies_saved_config_before_session_manager() -> None:
    cfg = AppConfig()
    events: list[str] = []

    manager = MagicMock()

    def create_manager(received_cfg):
        assert received_cfg is cfg
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


def test_webhook_uses_configured_session_manager() -> None:
    cfg = AppConfig()
    cfg.repos.auto_fetch = False
    manager = MagicMock()
    configured_manager = MagicMock(return_value=manager)

    def close_coroutine(coro) -> None:
        coro.close()

    with (
        patch.object(cli, "get_config", return_value=cfg),
        patch(
            "zd_agent.services.app_bootstrap.create_configured_session_manager",
            configured_manager,
        ),
        patch.object(cli.asyncio, "run", side_effect=close_coroutine),
    ):
        cli.webhook(host="127.0.0.1", port=18082)

    configured_manager.assert_called_once_with(cfg)
