#!/usr/bin/env bash
set -euo pipefail

# Deploy remote MCP Tool Services used by zd-agent.
#
# Services are intentionally separate systemd processes:
# - github-mcp          -> /github-mcp/mcp
# - docs-tool-service   -> /docs-tool-service/mcp
# - notification-service-> /notification-service/mcp
# - sandbox-worker      -> /sandbox-worker/mcp
#
# External credentials stay in each service env file under /etc/<service>/env.
# The agent env only receives remote MCP URLs and per-service API keys.

SRC_DIR="${TOOL_SERVICES_SRC_DIR:-/opt/zd-agent/code/deploy/tool-services}"
INSTALL_DIR="${TOOL_SERVICES_INSTALL_DIR:-/opt/tool-services/code}"
AGENT_ENV="${ZD_AGENT_ENV_FILE:-/opt/zd-agent/.env}"
PUBLIC_BASE="${TOOL_SERVICES_PUBLIC_BASE_URL:-https://zendesk.smlershou.top}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"

log() {
  printf '[tool-services] %s\n' "$*"
}

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Tool services source dir not found: $SRC_DIR" >&2
  exit 1
fi

log "Syncing source to ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
  --exclude '.venv' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'src/tool_services.egg-info' \
  "$SRC_DIR"/ "$INSTALL_DIR"/

log "Preparing virtualenv"
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi

log "Installing runtime dependencies"
PIP_INDEX_URL="$PIP_INDEX_URL" PIP_TRUSTED_HOST="$PIP_TRUSTED_HOST" \
  "$INSTALL_DIR/.venv/bin/pip" install --no-cache-dir \
    'fastapi>=0.115' \
    'httpx>=0.27' \
    'pydantic-settings>=2.0' \
    'uvicorn[standard]>=0.30' >/tmp/tool-services-pip-install.log

log "Writing service env and nginx locations"
TOOL_SERVICES_PUBLIC_BASE_URL="$PUBLIC_BASE" \
ZD_AGENT_ENV_FILE="$AGENT_ENV" \
TOOL_SERVICES_INSTALL_DIR="$INSTALL_DIR" \
python3 - <<'PY'
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

PUBLIC_BASE = os.environ["TOOL_SERVICES_PUBLIC_BASE_URL"].rstrip("/")
AGENT_ENV = Path(os.environ["ZD_AGENT_ENV_FILE"])
INSTALL_DIR = Path(os.environ["TOOL_SERVICES_INSTALL_DIR"])


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    existing = read_env(path)
    existing.update({k: v for k, v in updates.items() if v is not None})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"{key}={existing[key]}" for key in sorted(existing)) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def ensure_key(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    return value or secrets.token_urlsafe(32)


agent = read_env(AGENT_ENV)
services = {
    "github-mcp": Path("/etc/github-mcp/env"),
    "docs-tool-service": Path("/etc/docs-tool-service/env"),
    "notification-service": Path("/etc/notification-service/env"),
    "sandbox-worker": Path("/etc/sandbox-worker/env"),
}
service_envs = {name: read_env(path) for name, path in services.items()}

github_key = ensure_key(service_envs["github-mcp"], "GITHUB_MCP_API_KEY")
docs_key = ensure_key(service_envs["docs-tool-service"], "DOCS_TOOL_SERVICE_API_KEY")
notification_key = ensure_key(service_envs["notification-service"], "NOTIFICATION_SERVICE_API_KEY")
sandbox_key = ensure_key(service_envs["sandbox-worker"], "SANDBOX_WORKER_API_KEY")

write_env(
    services["github-mcp"],
    {
        "GITHUB_MCP_HOST": "127.0.0.1",
        "GITHUB_MCP_PORT": "8111",
        "GITHUB_MCP_API_KEY": github_key,
        "GITHUB_MCP_GITHUB_TOKEN": (
            service_envs["github-mcp"].get("GITHUB_MCP_GITHUB_TOKEN")
            or agent.get("GITHUB_TOKEN", "")
        ),
        "GITHUB_MCP_TIMEOUT_SECONDS": service_envs["github-mcp"].get(
            "GITHUB_MCP_TIMEOUT_SECONDS", "30"
        ),
    },
)
write_env(
    services["docs-tool-service"],
    {
        "DOCS_TOOL_SERVICE_HOST": "127.0.0.1",
        "DOCS_TOOL_SERVICE_PORT": "8112",
        "DOCS_TOOL_SERVICE_API_KEY": docs_key,
        "DOCS_TOOL_SERVICE_TIMEOUT_SECONDS": service_envs["docs-tool-service"].get(
            "DOCS_TOOL_SERVICE_TIMEOUT_SECONDS", "20"
        ),
        "DOCS_TOOL_SERVICE_CACHE_TTL_SECONDS": service_envs["docs-tool-service"].get(
            "DOCS_TOOL_SERVICE_CACHE_TTL_SECONDS", "900"
        ),
    },
)
write_env(
    services["notification-service"],
    {
        "NOTIFICATION_SERVICE_HOST": "127.0.0.1",
        "NOTIFICATION_SERVICE_PORT": "8113",
        "NOTIFICATION_SERVICE_API_KEY": notification_key,
        "NOTIFICATION_SERVICE_TIMEOUT_SECONDS": service_envs["notification-service"].get(
            "NOTIFICATION_SERVICE_TIMEOUT_SECONDS", "20"
        ),
        "FEISHU_WEBHOOK_URL": (
            service_envs["notification-service"].get("FEISHU_WEBHOOK_URL")
            or agent.get("FEISHU_WEBHOOK_URL", "")
        ),
        "FEISHU_WEBHOOK_SECRET": (
            service_envs["notification-service"].get("FEISHU_WEBHOOK_SECRET")
            or agent.get("FEISHU_WEBHOOK_SECRET", "")
        ),
    },
)
write_env(
    services["sandbox-worker"],
    {
        "SANDBOX_WORKER_HOST": "127.0.0.1",
        "SANDBOX_WORKER_PORT": "8114",
        "SANDBOX_WORKER_API_KEY": sandbox_key,
        "SANDBOX_WORKER_BASE_DIR": service_envs["sandbox-worker"].get(
            "SANDBOX_WORKER_BASE_DIR", "/opt/zd-agent/resources/sandbox"
        ),
        "SANDBOX_WORKER_SCRIPTS_DIR": service_envs["sandbox-worker"].get(
            "SANDBOX_WORKER_SCRIPTS_DIR", "/opt/zd-agent/tool-scripts"
        ),
        "SANDBOX_WORKER_TIMEOUT_SECONDS": service_envs["sandbox-worker"].get(
            "SANDBOX_WORKER_TIMEOUT_SECONDS", "60"
        ),
        "SANDBOX_WORKER_MAX_WRITE_BYTES": service_envs["sandbox-worker"].get(
            "SANDBOX_WORKER_MAX_WRITE_BYTES", "1048576"
        ),
    },
)

write_env(
    AGENT_ENV,
    {
        "GITHUB_MCP_URL": f"{PUBLIC_BASE}/github-mcp/mcp",
        "GITHUB_MCP_API_KEY": github_key,
        "GITHUB_MCP_TIMEOUT_SECONDS": agent.get("GITHUB_MCP_TIMEOUT_SECONDS", "30"),
        "DOCS_TOOL_SERVICE_URL": f"{PUBLIC_BASE}/docs-tool-service/mcp",
        "DOCS_TOOL_SERVICE_API_KEY": docs_key,
        "DOCS_TOOL_SERVICE_TIMEOUT_SECONDS": agent.get("DOCS_TOOL_SERVICE_TIMEOUT_SECONDS", "20"),
        "NOTIFICATION_SERVICE_URL": f"{PUBLIC_BASE}/notification-service/mcp",
        "NOTIFICATION_SERVICE_API_KEY": notification_key,
        "NOTIFICATION_SERVICE_TIMEOUT_SECONDS": agent.get("NOTIFICATION_SERVICE_TIMEOUT_SECONDS", "20"),
        "SANDBOX_WORKER_URL": f"{PUBLIC_BASE}/sandbox-worker/mcp",
        "SANDBOX_WORKER_API_KEY": sandbox_key,
        "SANDBOX_WORKER_TIMEOUT_SECONDS": agent.get("SANDBOX_WORKER_TIMEOUT_SECONDS", "60"),
    },
)

Path("/opt/zd-agent/resources/sandbox").mkdir(parents=True, exist_ok=True)

marker_start = "    # BEGIN zd-agent tool-service MCP endpoints"
marker_end = "    # END zd-agent tool-service MCP endpoints"
block = f"""{marker_start}
    location /github-mcp/ {{
        proxy_pass http://127.0.0.1:8111/;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }}

    location /docs-tool-service/ {{
        proxy_pass http://127.0.0.1:8112/;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }}

    location /notification-service/ {{
        proxy_pass http://127.0.0.1:8113/;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }}

    location /sandbox-worker/ {{
        proxy_pass http://127.0.0.1:8114/;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }}
{marker_end}
"""

candidates = [
    Path("/etc/nginx/sites-enabled/zd-agent.conf"),
    Path("/etc/nginx/sites-available/zd-agent.conf"),
]
seen: set[Path] = set()
for conf in candidates:
    if not conf.exists():
        continue
    resolved = conf.resolve()
    if resolved in seen:
        continue
    seen.add(resolved)
    text = conf.read_text(encoding="utf-8")
    if marker_start in text and marker_end in text:
        before, rest = text.split(marker_start, 1)
        _, after = rest.split(marker_end, 1)
        new_text = before + block + after.lstrip("\n")
    else:
        needle = "    # Health check"
        if needle not in text:
            continue
        new_text = text.replace(needle, block + "\n" + needle, 1)
    shutil.copy2(conf, conf.with_name(f"{conf.name}.bak-tool-services"))
    conf.write_text(new_text, encoding="utf-8")

for unit in (INSTALL_DIR / "deploy/systemd").glob("*.service"):
    shutil.copy2(unit, Path("/etc/systemd/system") / unit.name)
PY

log "Starting systemd services"
systemctl daemon-reload
systemctl enable --now github-mcp.service docs-tool-service.service notification-service.service sandbox-worker.service
systemctl restart github-mcp.service docs-tool-service.service notification-service.service sandbox-worker.service

log "Reloading nginx"
nginx -t
systemctl reload nginx

log "Verifying remote MCP endpoints"
TOOL_SERVICES_PUBLIC_BASE_URL="$PUBLIC_BASE" python3 - <<'PY'
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

public_base = os.environ.get("TOOL_SERVICES_PUBLIC_BASE_URL", "https://zendesk.smlershou.top").rstrip("/")
services = [
    ("github-mcp", "GITHUB_MCP_API_KEY", "github-mcp"),
    ("docs-tool-service", "DOCS_TOOL_SERVICE_API_KEY", "docs-tool-service"),
    ("notification-service", "NOTIFICATION_SERVICE_API_KEY", "notification-service"),
    ("sandbox-worker", "SANDBOX_WORKER_API_KEY", "sandbox-worker"),
]


def read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


for service, key_name, path in services:
    env = read_env(f"/etc/{service}/env")
    req = urllib.request.Request(
        f"{public_base}/{path}/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {env[key_name]}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    tools = data["result"]["tools"]
    print(f"{service}: remote-ok ({len(tools)} tools)")
PY

log "Deployment complete"
