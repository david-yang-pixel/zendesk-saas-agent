from pathlib import Path

from deploy.nginx_switch import render_greenfield, switch

LEGACY = """server {
    location /webhook/ {
        proxy_pass http://127.0.0.1:8080;
    }
    location = /healthz {
        proxy_pass http://127.0.0.1:8080;
        access_log off;
    }
    location = /admin {
        proxy_pass http://127.0.0.1:8080;
    }
    location /admin/ {
        proxy_pass http://127.0.0.1:8080;
    }
    location /agent/ {
        proxy_pass http://127.0.0.1:8080;
    }
    location /source-mcp/ {
        proxy_pass http://127.0.0.1:8099/;
    }
}
"""


def test_render_switches_only_casework_public_routes() -> None:
    rendered = render_greenfield(LEGACY)
    assert "18101/webhook/zendesk" in rendered
    assert "18101/webhook/zendesk-messaging" in rendered
    assert "18101/healthz" in rendered
    assert "proxy_pass http://127.0.0.1:18104/;" in rendered
    assert "location /webhook/" not in rendered
    assert "location /agent/" not in rendered
    assert "127.0.0.1:8080" not in rendered
    assert "location /source-mcp/" in rendered
    assert "127.0.0.1:8099" in rendered
    assert render_greenfield(rendered) == rendered


def test_switch_keeps_only_transient_backup(tmp_path: Path) -> None:
    config = tmp_path / "zd-agent.conf"
    backup = tmp_path / "legacy.conf"
    config.write_text(LEGACY)
    switch(config=config, backup=backup)
    assert config.read_text() != LEGACY
    assert backup.read_text() == LEGACY
