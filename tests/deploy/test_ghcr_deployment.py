from __future__ import annotations

import os
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "docker-image.yml"
DEPLOY_SCRIPT = ROOT / "deploy" / "deploy-ghcr.sh"


def test_github_action_builds_and_pushes_main_and_sha_tags() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)

    assert "branches: [main]" in text
    assert "workflow_dispatch:" in text
    assert workflow["permissions"] == {"contents": "read", "packages": "write"}
    assert workflow["env"]["IMAGE_NAME"] == "ghcr.io/yang-dify/zendesk-saas-agent"
    assert "docker/login-action@v3" in text
    assert "docker/build-push-action@v6" in text
    assert "file: deploy/Dockerfile" in text
    assert "${{ env.IMAGE_NAME }}:main" in text
    assert "${{ env.IMAGE_NAME }}:sha-${{ github.sha }}" in text


def test_ghcr_deploy_script_keeps_secret_out_of_source_and_retags_latest() -> None:
    text = DEPLOY_SCRIPT.read_text()

    assert os.access(DEPLOY_SCRIPT, os.X_OK)
    assert "GHCR_PAT" in text
    assert "read:packages" in text
    assert "docker login ghcr.io" in text
    assert "docker pull \"$REMOTE_IMAGE\"" in text
    assert "docker tag \"$REMOTE_IMAGE\" \"$LOCAL_IMAGE\"" in text
    assert 'LOCAL_IMAGE="${ZD_AGENT_LOCAL_IMAGE:-zd-agent:latest}"' in text
    assert "systemctl restart \"$SERVICE\"" in text
    assert "curl -fsS \"$HEALTH_URL\"" in text
