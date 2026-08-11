from __future__ import annotations

from zd_agent.config import load_config


def test_pytest_masks_notification_service_from_dotenv(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "NOTIFICATION_SERVICE_URL=https://ops.example.com/notification-service",
                "NOTIFICATION_SERVICE_API_KEY=real-key",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    cfg = load_config(config_path=str(tmp_path / "missing-config.yaml"))

    assert cfg.notification_service.mcp_url == ""
    assert cfg.notification_service.mcp_api_key == ""
