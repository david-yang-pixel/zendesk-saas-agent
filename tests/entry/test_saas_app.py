"""Test SaaS app factory creates app with expected routes."""
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_create_saas_app_has_expected_routes(tmp_path):
    from zd_agent.entry.saas_app import create_saas_app

    with patch("zd_agent.entry.saas_app.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.resources.base_dir = str(tmp_path)
        cfg.resources.state_dir = ".state"
        cfg.webhook.zendesk_path = "/webhook/zendesk"
        mock_cfg.return_value = cfg

        manager = MagicMock()
        app = create_saas_app(manager)

    route_paths = {r.resource.canonical for r in app.router.routes()}
    assert "/webhook/zendesk" in route_paths
    assert "/healthz" in route_paths
    assert "/webhook/feishu" not in route_paths


def test_create_saas_app_wires_queues_and_state(tmp_path):
    from zd_agent.entry.saas_app import create_saas_app

    with patch("zd_agent.entry.saas_app.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.resources.base_dir = str(tmp_path)
        cfg.resources.state_dir = ".state"
        cfg.webhook.zendesk_path = "/webhook/zendesk"
        mock_cfg.return_value = cfg

        manager = MagicMock()
        app = create_saas_app(manager)

    assert "trigger_queue" in app
    assert "feishu_command_queue" not in app
    assert "ticket_state" in app
    assert "webhook_dedup" in app
    assert app["session_manager"] is manager
