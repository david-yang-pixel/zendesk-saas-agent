from pathlib import Path

from deploy.nginx_switch import render_admin_greenfield, render_greenfield, switch

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
    assert "location = /webhook/zendesk-ticket-events {" in rendered
    assert "18101/webhook/zendesk-ticket-events;" in rendered
    assert "18101/healthz" in rendered
    assert "proxy_pass http://127.0.0.1:18104/;" in rendered
    assert "location /webhook/" not in rendered
    assert "location /agent/" not in rendered
    assert "127.0.0.1:8080" not in rendered
    assert "location /source-mcp/" in rendered
    assert "127.0.0.1:8099" in rendered
    assert render_greenfield(rendered) == rendered


def test_upgrade_existing_greenfield_routes_preserves_other_locations() -> None:
    from deploy.nginx_switch import BEGIN, END

    current = render_greenfield(LEGACY)
    old = current[:current.index(BEGIN)] + BEGIN + "\n    # old routes\n" + current[current.index(END):]
    assert render_greenfield(old) == current


def test_switch_keeps_only_transient_backup(tmp_path: Path) -> None:
    config = tmp_path / "zd-agent.conf"
    backup = tmp_path / "legacy.conf"
    config.write_text(LEGACY)
    switch(config=config, backup=backup)
    assert config.read_text() != LEGACY
    assert backup.read_text() == LEGACY


def test_admin_only_switch_preserves_legacy_agent_routes() -> None:
    rendered = render_admin_greenfield(LEGACY)

    assert "proxy_pass http://127.0.0.1:18104/;" in rendered
    assert "Content-Security-Policy" in rendered
    assert "location /webhook/" in rendered
    assert "location /agent/" in rendered
    assert "127.0.0.1:8080" in rendered
