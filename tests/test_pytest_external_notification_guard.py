from __future__ import annotations

from zd_agent.config import load_config


def test_pytest_masks_feishu_webhook_from_dotenv(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/real",
                "FEISHU_WEBHOOK_SECRET=real-secret",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    cfg = load_config(config_path=str(tmp_path / "missing-config.yaml"))

    assert cfg.feishu.webhook_url == ""
    assert cfg.feishu.webhook_secret == ""
