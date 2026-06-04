import importlib
from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    control_path = tmp_path / "control.json"
    audit_path = tmp_path / "admin-audit.jsonl"

    config_path.write_text(
        "\n".join(
            [
                "llm:",
                "  provider: openai",
                "  model: gpt-5.5",
                "  subagent_model: gpt-5.4-mini",
                "  base_url: https://api.openai.test/v1",
                "trigger_decider:",
                "  llm_model: gpt-5.5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env_path.write_text(
        "OPENAI_API_KEY=sk-oldsecret1234\nANTHROPIC_API_KEY=\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("ZD_LOG_DIR", str(log_dir))
    monkeypatch.setenv("ZD_AGENT_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ZD_AGENT_ENV_PATH", str(env_path))
    monkeypatch.setenv("ZD_AGENT_CONTROL_PATH", str(control_path))
    monkeypatch.setenv("ZD_AGENT_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("ZD_AGENT_SERVICE", "zd-agent")

    import main

    importlib.reload(main)
    return TestClient(main.app), config_path, env_path, control_path, audit_path, main


def test_admin_config_masks_api_keys(tmp_path, monkeypatch):
    client, _, _, _, _, _ = _client(tmp_path, monkeypatch)

    response = client.get("/api/admin/config")

    assert response.status_code == 200
    data = response.json()
    assert data["llm"]["model"] == "gpt-5.5"
    assert data["trigger_decider"]["llm_model"] == "gpt-5.5"
    assert data["api_keys"]["openai"]["configured"] is True
    assert data["api_keys"]["openai"]["masked"].endswith("1234")
    assert "oldsecret" not in response.text
    assert "sk-oldsecret1234" not in response.text


def test_admin_config_update_models_without_overwriting_blank_key(tmp_path, monkeypatch):
    client, config_path, env_path, _, audit_path, _ = _client(tmp_path, monkeypatch)

    response = client.put(
        "/api/admin/config",
        json={
            "llm": {
                "provider": "openai",
                "model": "gpt-5.6",
                "subagent_model": "gpt-5.6-mini",
                "base_url": "https://api.openai.test/v2",
            },
            "trigger_decider": {"llm_model": "gpt-5.6"},
            "api_keys": {"openai": ""},
        },
        headers={"X-Auth-Request-Email": "ops@dify.ai", "X-Forwarded-For": "1.2.3.4"},
    )

    assert response.status_code == 200
    assert response.json()["restart_required"] is True
    assert "sk-oldsecret1234" in env_path.read_text(encoding="utf-8")
    written = config_path.read_text(encoding="utf-8")
    assert "model: gpt-5.6" in written
    assert "subagent_model: gpt-5.6-mini" in written
    assert "llm_model: gpt-5.6" in written
    audit = audit_path.read_text(encoding="utf-8")
    assert "ops@dify.ai" in audit
    assert "1.2.3.4" in audit
    assert "sk-oldsecret1234" not in audit


def test_admin_config_update_new_key_never_returns_secret(tmp_path, monkeypatch):
    client, _, env_path, _, _, _ = _client(tmp_path, monkeypatch)
    new_key = "sk-newsecret5678"

    response = client.put(
        "/api/admin/config",
        json={"api_keys": {"openai": new_key}},
        headers={"X-Auth-Request-Email": "ops@dify.ai"},
    )

    assert response.status_code == 200
    assert new_key in env_path.read_text(encoding="utf-8")
    assert new_key not in response.text


def test_admin_control_pause_and_resume(tmp_path, monkeypatch):
    client, _, _, control_path, audit_path, _ = _client(tmp_path, monkeypatch)

    paused = client.post(
        "/api/admin/control/pause",
        headers={"X-Auth-Request-Email": "ops@dify.ai"},
    )
    assert paused.status_code == 200
    assert paused.json()["control"]["paused"] is True
    assert '"paused": true' in control_path.read_text(encoding="utf-8")

    resumed = client.post(
        "/api/admin/control/resume",
        headers={"X-Auth-Request-Email": "ops@dify.ai"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["control"]["paused"] is False
    assert "control.pause" in audit_path.read_text(encoding="utf-8")
    assert "control.resume" in audit_path.read_text(encoding="utf-8")


def test_admin_restart_uses_systemctl_with_mock(tmp_path, monkeypatch):
    client, _, _, _, audit_path, main = _client(tmp_path, monkeypatch)
    calls = []

    def fake_restart(service):
        calls.append(service)
        return {"ok": True, "returncode": 0}

    monkeypatch.setattr(main.A, "restart_service", fake_restart)

    response = client.post(
        "/api/admin/service/restart",
        headers={"X-Auth-Request-Email": "ops@dify.ai"},
    )

    assert response.status_code == 200
    assert calls == ["zd-agent"]
    assert response.json()["ok"] is True
    assert "service.restart" in audit_path.read_text(encoding="utf-8")


def test_admin_audit_reads_recent_records(tmp_path, monkeypatch):
    client, _, _, _, audit_path, _ = _client(tmp_path, monkeypatch)
    audit_path.write_text(
        '{"timestamp":"2026-06-04T00:00:00Z","action":"x","actor_email":"ops@dify.ai"}\n',
        encoding="utf-8",
    )

    response = client.get("/api/admin/audit")

    assert response.status_code == 200
    assert response.json()["items"][0]["actor_email"] == "ops@dify.ai"
