"""Atomically replace legacy Agent routes with Greenfield routes."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

BEGIN = "    # BEGIN casework-greenfield routes"
END = "    # END casework-greenfield routes"

GREENFIELD_ROUTES = """    # BEGIN casework-greenfield routes
    location = /webhook/zendesk {
        proxy_pass http://127.0.0.1:18101/webhook/zendesk;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    location = /webhook/zendesk-messaging {
        proxy_pass http://127.0.0.1:18101/webhook/zendesk-messaging;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
    # END casework-greenfield routes

"""


def _location_span(text: str, header: str) -> tuple[int, int]:
    start = text.find(header)
    if start < 0:
        raise RuntimeError(f"Nginx configuration is missing {header.strip()}")
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError(f"Nginx location has no opening brace: {header.strip()}")
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                return start, end
    raise RuntimeError(f"Nginx location is unterminated: {header.strip()}")


def _remove_optional_location(text: str, header: str) -> str:
    if header not in text:
        return text
    start, end = _location_span(text, header)
    return text[:start] + text[end:]


def render_greenfield(text: str) -> str:
    if BEGIN in text:
        return text
    text = text.replace(
        "# HTTPS reverse proxy to 127.0.0.1:8080 (zd-agent container)",
        "# HTTPS routes for Casework Greenfield and supporting tool services",
    )
    webhook_start, webhook_end = _location_span(text, "    location /webhook/ {")
    text = text[:webhook_start] + GREENFIELD_ROUTES + text[webhook_end:]
    text = _remove_optional_location(text, "    location /agent/ {")

    health_start, health_end = _location_span(text, "    location = /healthz {")
    health = """    location = /healthz {
        proxy_pass http://127.0.0.1:18101/healthz;
        access_log off;
    }
"""
    text = text[:health_start] + health + text[health_end:]

    admin_start, admin_end = _location_span(text, "    location = /admin {")
    text = text[:admin_start] + text[admin_end:]
    admin_slash_start, admin_slash_end = _location_span(text, "    location /admin/ {")
    admin = """    location = /admin {
        return 308 /admin/;
    }

    location /admin/ {
        proxy_pass http://127.0.0.1:18104/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        add_header X-Frame-Options DENY always;
        add_header X-Content-Type-Options nosniff always;
        add_header Referrer-Policy no-referrer always;
        add_header Cache-Control "no-store" always;
    }
"""
    return text[:admin_slash_start] + admin + text[admin_slash_end:]


def switch(*, config: Path, backup: Path) -> None:
    if not backup.exists():
        shutil.copy2(config, backup)
        backup.chmod(0o600)
    candidate = render_greenfield(config.read_text())

    temporary = config.with_name(f".{config.name}.casework.tmp")
    temporary.write_text(candidate)
    temporary.chmod(config.stat().st_mode & 0o777)
    os.replace(temporary, config)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=Path("/etc/nginx/sites-enabled/zd-agent.conf")
    )
    parser.add_argument(
        "--backup",
        type=Path,
        default=Path("/opt/zd-agent/greenfield/nginx.legacy.conf"),
    )
    args = parser.parse_args()
    switch(config=args.config, backup=args.backup)


if __name__ == "__main__":
    main()
