import pytest

from zd_agent.runtime.log_reporter import LogReporter


@pytest.mark.asyncio
async def test_report_run_start_marks_saas_source(monkeypatch):
    monkeypatch.setenv("HC_CLIENT_URL", "https://hc.example.com")
    monkeypatch.setenv("HC_CLIENT_API_KEY", "agent-key")
    captured = {}

    async def fake_post(path, data):
        captured["path"] = path
        captured["data"] = data
        return {"id": 123}

    reporter = LogReporter()
    monkeypatch.setattr(reporter, "_post", fake_post)

    run_id = await reporter.report_run_start(
        ticket_id=2849,
        session_id="session-1",
        trigger_type="webhook",
        model="gpt-5.4",
    )

    assert run_id == 123
    assert captured["path"] == "/api/agent/logs/runs"
    assert captured["data"]["source"] == "saas"
