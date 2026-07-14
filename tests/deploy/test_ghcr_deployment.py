from __future__ import annotations

import os
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "docker-image.yml"
DEPLOY_SCRIPT = ROOT / "deploy" / "deploy-ghcr.sh"
NGINX_CONF = ROOT / "deploy" / "nginx" / "zd-agent.conf"
RUNBOOK = ROOT / "deploy" / "RUNBOOK.md"


def test_github_action_builds_and_pushes_main_and_sha_tags() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)

    assert "branches: [main]" in text
    assert "workflow_dispatch:" in text
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["IMAGE_NAME"] == "ghcr.io/${{ github.repository }}"
    assert workflow["concurrency"] == {
        "group": "zd-agent-${{ github.ref }}",
        "cancel-in-progress": True,
    }
    assert workflow["jobs"]["build"]["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["jobs"]["build"]["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    assert "docker/login-action@v3" in text
    assert "docker/build-push-action@v6" in text
    assert "file: deploy/Dockerfile" in text
    assert "${{ env.IMAGE_NAME }}:main" in text
    assert "${{ env.IMAGE_NAME }}:sha-${{ github.sha }}" in text


def test_github_action_gates_image_build_on_quality_checks() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    quality_job = workflow["jobs"]["quality"]

    assert workflow["jobs"]["build"]["needs"] == "quality"
    assert quality_job["if"] == (
        "github.event_name == 'pull_request' || github.ref == 'refs/heads/main'"
    )
    assert quality_job["permissions"] == {"contents": "read"}
    assert "astral-sh/setup-uv@v6" in text
    assert "version: \"0.10.7\"" in text
    assert "uv sync --frozen --dev" in text
    assert "uv run ruff check src" in text
    assert "uv run pytest -q" in text


def test_github_action_runs_quality_checks_for_main_pull_requests() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)

    assert "pull_request:" in text
    assert "branches: [main]" in text
    assert workflow["jobs"]["build"]["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["jobs"]["deploy"]["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )


def test_ghcr_deploy_script_keeps_secret_out_of_source_and_retags_latest() -> None:
    text = DEPLOY_SCRIPT.read_text()

    assert os.access(DEPLOY_SCRIPT, os.X_OK)
    assert (
        'IMAGE="${ZD_AGENT_IMAGE:-ghcr.io/david-yang-pixel/zendesk-saas-agent}"'
        in text
    )
    assert "GHCR_PAT" in text
    assert "read:packages" in text
    assert "docker login ghcr.io" in text
    assert "docker pull \"$REMOTE_IMAGE\"" in text
    assert "docker tag \"$REMOTE_IMAGE\" \"$LOCAL_IMAGE\"" in text
    assert 'LOCAL_IMAGE="${ZD_AGENT_LOCAL_IMAGE:-zd-agent:latest}"' in text
    assert "systemctl restart \"$SERVICE\"" in text
    assert "curl -fsS \"$HEALTH_URL\"" in text


def test_docker_runtime_starts_prebuilt_agent_without_dependency_sync() -> None:
    text = (ROOT / "deploy" / "Dockerfile").read_text()

    assert 'CMD ["/app/.venv/bin/zd-agent", "serve"]' in text
    assert 'CMD ["uv", "run"' not in text


def test_github_action_deploys_main_image_over_ssh_after_build() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    deploy_job = workflow["jobs"]["deploy"]

    assert deploy_job["needs"] == "build"
    assert deploy_job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert deploy_job["permissions"] == {"contents": "read", "packages": "read"}
    assert deploy_job["environment"] == "production"
    assert "${{ secrets.ZD_AGENT_DEPLOY_HOST }}" in text
    assert "${{ secrets.ZD_AGENT_DEPLOY_USER }}" in text
    assert "${{ secrets.ZD_AGENT_DEPLOY_SSH_KEY }}" in text
    assert "${{ secrets.ZD_AGENT_DEPLOY_KNOWN_HOSTS }}" in text
    assert "GHCR_PAT: ${{ secrets.GITHUB_TOKEN }}" in text
    assert "export GHCR_PAT=" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "ssh-keyscan" not in text
    assert "git merge --ff-only origin/main" in text
    assert "git pull --ff-only origin main" not in text
    assert 'DEPLOY_TAG="sha-${GITHUB_SHA}"' in text
    assert './deploy/deploy-ghcr.sh "$DEPLOY_TAG"' in text
    assert "docker build" not in deploy_job["steps"][0]["run"]
    assert "packages: write" not in yaml.dump(deploy_job)
    assert "set -x" not in text
    assert "printenv" not in text


def test_github_action_retries_transient_server_git_fetch_failures() -> None:
    text = WORKFLOW.read_text()

    assert "for attempt in 1 2 3; do" in text
    assert "if timeout 30s git fetch origin main; then" in text
    assert 'echo "git fetch failed after ${attempt} attempts" >&2' in text
    assert "sleep $((attempt * 5))" in text
    assert "timeout-minutes: 10" in text


def test_github_action_sends_success_email_only_after_deploy() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    notify_job = workflow["jobs"]["notify-success"]

    assert notify_job["needs"] == "deploy"
    assert notify_job["if"] == "success()"
    assert notify_job["permissions"] == {"contents": "read"}
    assert "${{ secrets.DEPLOY_NOTIFY_SMTP_HOST }}" in text
    assert "${{ secrets.DEPLOY_NOTIFY_SMTP_PORT }}" in text
    assert "${{ secrets.DEPLOY_NOTIFY_SMTP_USERNAME }}" in text
    assert "${{ secrets.DEPLOY_NOTIFY_SMTP_PASSWORD }}" in text
    assert "${{ secrets.DEPLOY_NOTIFY_EMAIL_FROM }}" in text
    assert "${{ secrets.DEPLOY_NOTIFY_EMAIL_TO }}" in text
    assert "smtplib.SMTP_SSL" in text
    assert "send_message(msg)" in text
    assert "zd-agent 部署成功" in text
    assert "zd-agent 生产环境部署已成功完成。" in text
    assert "仓库：" in text
    assert "提交：" in text
    assert "已部署镜像：" in text
    assert "同步更新标签：" in text
    assert "工作流运行：" in text
    assert "Skipping deployment success email" in text


def test_nginx_exposes_admin_console_without_exposing_internal_routes() -> None:
    text = NGINX_CONF.read_text()

    assert "location = /admin" in text
    assert "location /admin/" in text
    assert "proxy_pass http://127.0.0.1:8080;" in text
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in text
    assert "add_header X-Frame-Options DENY always;" in text
    assert "add_header X-Content-Type-Options nosniff always;" in text
    assert "location /internal/" in text
    assert "return 404;" in text


def test_runbook_documents_zendesk_mcp_attachment_download_limit() -> None:
    text = RUNBOOK.read_text()

    assert "ZENDESK_MCP_DOWNLOAD_MAX_BYTES" in text
    assert "download_attachment" in text
    assert "/opt/zendesk-mcp/.env" in text
