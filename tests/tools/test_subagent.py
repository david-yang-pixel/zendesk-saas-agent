import sys
import types

import pytest

from zd_agent.tools.subagent import RunSubagent


def _install_fake_subagent(monkeypatch, fake_fn):
    mod = types.ModuleType("zd_agent.runtime.subagent")
    mod.run_subagent = fake_fn
    monkeypatch.setitem(sys.modules, "zd_agent.runtime.subagent", mod)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({}, "task is required"),
        ({"task": ["analyze ticket"]}, "task must be a string"),
        ({"task": "analyze ticket", "tools": "zendesk_get_ticket"}, "tools must be a list of strings"),
        ({"task": "analyze ticket", "tools": ["zendesk_get_ticket", 123]}, "tools must be a list of strings"),
        ({"task": "analyze ticket", "context": {"ticket_id": 123}}, "context must be a string"),
    ],
)
async def test_run_subagent_rejects_invalid_args_before_dispatch(
    fake_session,
    monkeypatch,
    args,
    expected,
):
    async def fake_run_subagent(*_args, **_kwargs):
        raise AssertionError("run_subagent should not be called")

    _install_fake_subagent(monkeypatch, fake_run_subagent)

    result = await RunSubagent().execute(args, fake_session)

    assert result.success is False
    assert result.error == expected
