"""Test TriggerDecider.classify_messages with mocked LLM."""
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.trigger.decider import (
    PreCheckVerdict,
    classify_messages,
)


@pytest.mark.asyncio
async def test_classify_greeting():
    llm_resp = '{"verdict": "greeting", "completeness": 10, "reason": "only hi"}'
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["hi"], model="gpt-5.4-mini", api_key="x",
        )
    assert isinstance(v, PreCheckVerdict)
    assert v.verdict == "greeting"
    assert v.completeness == 10
    assert v.reason == "only hi"


@pytest.mark.asyncio
async def test_classify_substantial_issue():
    llm_resp = (
        '{"verdict": "substantial_issue", "completeness": 85, '
        '"reason": "wid + error message present"}'
    )
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["workflow returns 500 on /v1/workflows, wid=abc"],
            model="gpt-5.4-mini", api_key="x",
        )
    assert v.verdict == "substantial_issue"
    assert v.completeness == 85


@pytest.mark.asyncio
async def test_classify_insufficient():
    llm_resp = (
        '{"verdict": "insufficient_info", "completeness": 45, '
        '"reason": "no error text or wid"}'
    )
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["knowledge retrieval broken"],
            model="gpt-5.4-mini", api_key="x",
        )
    assert v.verdict == "insufficient_info"


@pytest.mark.asyncio
async def test_empty_messages_returns_insufficient():
    v = await classify_messages(messages=[], model="gpt-5.4-mini", api_key="x")
    assert v.verdict == "insufficient_info"
    assert v.completeness == 0


@pytest.mark.asyncio
async def test_llm_error_fallback():
    with patch(
        "zd_agent.trigger.decider._call_llm",
        AsyncMock(side_effect=RuntimeError("timeout")),
    ):
        v = await classify_messages(
            messages=["x"], model="gpt-5.4-mini", api_key="x",
        )
    assert v.verdict == "insufficient_info"
    assert v.completeness == 50
    assert "LLM error" in v.reason


@pytest.mark.asyncio
async def test_invalid_json_fallback():
    with patch(
        "zd_agent.trigger.decider._call_llm",
        AsyncMock(return_value="not json at all"),
    ):
        v = await classify_messages(
            messages=["x"], model="gpt-5.4-mini", api_key="x",
        )
    assert v.verdict == "insufficient_info"
    assert v.completeness == 50
    assert "invalid JSON" in v.reason


@pytest.mark.asyncio
async def test_unknown_verdict_coerced_to_insufficient():
    llm_resp = '{"verdict": "bogus", "completeness": 70, "reason": "x"}'
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["x"], model="gpt-5.4-mini", api_key="x",
        )
    assert v.verdict == "insufficient_info"
    # completeness still honored even if verdict is bogus
    assert v.completeness == 70


@pytest.mark.asyncio
async def test_completeness_clamped_to_0_100():
    llm_resp = '{"verdict": "greeting", "completeness": 150, "reason": "x"}'
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["x"], model="gpt-5.4-mini", api_key="x",
        )
    assert v.completeness == 100


@pytest.mark.asyncio
async def test_only_last_5_messages_sent_to_llm():
    many = [f"msg {i}" for i in range(10)]
    captured_prompt = []

    async def fake_llm(*, prompt, model, api_key, timeout=30.0):
        captured_prompt.append(prompt)
        return '{"verdict": "greeting", "completeness": 20, "reason": "x"}'

    with patch("zd_agent.trigger.decider._call_llm", fake_llm):
        await classify_messages(messages=many, model="gpt-5.4-mini", api_key="x")
    assert captured_prompt
    # only msg 5..9 should be in the prompt
    for i in range(5, 10):
        assert f"msg {i}" in captured_prompt[0]
    assert "msg 4" not in captured_prompt[0]
