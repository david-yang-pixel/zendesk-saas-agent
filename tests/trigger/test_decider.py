"""Test TriggerDecider.classify_messages with mocked LLM."""
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.trigger.decider import (
    PreCheckVerdict,
    _call_llm,
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
async def test_classify_passes_reasoning_effort_to_llm_call():
    llm_resp = '{"verdict": "greeting", "completeness": 10, "reason": "only hi"}'
    call_llm = AsyncMock(return_value=llm_resp)
    with patch("zd_agent.trigger.decider._call_llm", call_llm):
        await classify_messages(
            messages=["hi"],
            model="gpt-5.6-terra",
            reasoning_effort="max",
            api_key="x",
        )

    assert call_llm.await_args.kwargs["reasoning_effort"] == "max"


@pytest.mark.asyncio
async def test_call_llm_sends_configured_reasoning_effort():
    response = AsyncMock()
    response.status_code = 200
    response.json = lambda: {
        "choices": [{"message": {"content": '{"verdict":"greeting"}'}}],
    }
    post = AsyncMock(return_value=response)
    with patch("httpx.AsyncClient.post", post):
        await _call_llm(
            user_content="hello",
            model="gpt-5.6-terra",
            api_key="x",
            reasoning_effort="xhigh",
        )

    assert post.await_args.kwargs["json"]["reasoning_effort"] == "xhigh"
    assert post.await_args.kwargs["json"]["max_completion_tokens"] == 16384


@pytest.mark.asyncio
async def test_classify_empty_llm_content_falls_back_without_crashing():
    with patch("zd_agent.trigger.decider._call_llm", AsyncMock(return_value=None)):
        verdict = await classify_messages(
            messages=["Can you help?"],
            model="gpt-5.6-terra",
            api_key="x",
        )

    assert verdict.verdict == "insufficient_info"
    assert verdict.reason == "invalid JSON from LLM"


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
        '"reason": "unclear request"}'
    )
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["can you help me?"],
            model="gpt-5.4-mini", api_key="x",
        )
    assert v.verdict == "insufficient_info"


@pytest.mark.asyncio
async def test_account_email_change_error_upgrades_to_substantial_issue():
    llm_resp = (
        '{"verdict": "insufficient_info", "completeness": 55, '
        '"reason": "missing exact error text"}'
    )
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=[
                "I want to change my email address, but I'm getting an error "
                "message even though I'm following the correct procedure, so "
                "I can't change it."
            ],
            model="gpt-5.4-mini",
            api_key="x",
        )
    assert v.verdict == "substantial_issue"
    assert v.completeness == 65
    assert "账号邮箱变更失败" in v.reason


@pytest.mark.asyncio
async def test_actionable_refund_request_upgrades_to_substantial_issue():
    llm_resp = (
        '{"verdict": "insufficient_info", "completeness": 45, '
        '"reason": "missing invoice id"}'
    )
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["I need a refund for my subscription renewal."],
            model="gpt-5.4-mini",
            api_key="x",
        )
    assert v.verdict == "substantial_issue"
    assert v.completeness == 65
    assert v.high_risk_category == "refund"
    assert "明确诉求" in v.reason


@pytest.mark.asyncio
async def test_actionable_how_to_request_upgrades_to_substantial_issue():
    llm_resp = (
        '{"verdict": "insufficient_info", "completeness": 50, '
        '"reason": "missing workspace id"}'
    )
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["How do I configure Jina rerank in Knowledge Base?"],
            model="gpt-5.4-mini",
            api_key="x",
        )
    assert v.verdict == "substantial_issue"
    assert v.completeness == 65
    assert "明确诉求" in v.reason


@pytest.mark.asyncio
async def test_billing_how_to_request_is_not_refund_risk():
    llm_resp = (
        '{"verdict": "insufficient_info", "completeness": 50, '
        '"reason": "missing workspace id"}'
    )
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["How do I update the billing address on my subscription?"],
            model="gpt-5.4-mini",
            api_key="x",
        )
    assert v.verdict == "substantial_issue"
    assert v.high_risk_category is None


@pytest.mark.asyncio
async def test_generic_help_request_stays_insufficient_info():
    llm_resp = (
        '{"verdict": "insufficient_info", "completeness": 35, '
        '"reason": "no concrete topic"}'
    )
    with patch(
        "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
    ):
        v = await classify_messages(
            messages=["Can you help me?"],
            model="gpt-5.4-mini",
            api_key="x",
        )
    assert v.verdict == "insufficient_info"
    assert v.completeness == 35


@pytest.mark.asyncio
async def test_empty_messages_returns_no_message():
    """空 messages + 无截图 → no_message verdict（2026-05-13 M2 引入的新行为）"""
    v = await classify_messages(messages=[], model="gpt-5.4-mini", api_key="x")
    assert v.verdict == "no_message"
    assert v.completeness == 0
    assert v.high_risk_category is None


@pytest.mark.asyncio
async def test_llm_error_fallback():
    sensitive_error = "timeout data:image/png;base64,SECRET-CUSTOMER-CONTENT"
    with patch(
        "zd_agent.trigger.decider._call_llm",
        AsyncMock(side_effect=RuntimeError(sensitive_error)),
    ), patch("zd_agent.trigger.decider.logger.warning") as warning:
        v = await classify_messages(
            messages=["x"], model="gpt-5.4-mini", api_key="x",
        )
    assert v.verdict == "insufficient_info"
    assert v.completeness == 50
    assert "LLM error" in v.reason
    assert sensitive_error not in v.reason
    assert sensitive_error not in str(warning.call_args)


@pytest.mark.asyncio
async def test_llm_error_fallback_keeps_actionable_chinese_bug_substantial():
    with patch(
        "zd_agent.trigger.decider._call_llm",
        AsyncMock(side_effect=RuntimeError("401 invalid api key")),
    ):
        v = await classify_messages(
            messages=["为什么有些 swagger api 工具调用，并发调用会丢失鉴权信息，严重 bug"],
            model="gpt-5.4-mini",
            api_key="x",
        )
    assert v.verdict == "substantial_issue"
    assert v.completeness == 65
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
async def test_fenced_json_from_openai_compatible_model_is_parsed():
    raw = """```json
{
  "verdict": "substantial_issue",
  "completeness": 65,
  "high_risk_category": null,
  "reason": "客户明确描述 swagger API 工具并发调用丢失鉴权信息的具体 bug"
}
```"""
    with patch("zd_agent.trigger.decider._call_llm", AsyncMock(return_value=raw)):
        v = await classify_messages(
            messages=["为什么有些 swagger api 工具调用，并发调用会丢失鉴权信息，严重 bug"],
            model="gpt-5.4-mini",
            api_key="x",
        )
    assert v.verdict == "substantial_issue"
    assert v.completeness == 65


@pytest.mark.asyncio
async def test_invalid_json_fallback_keeps_actionable_chinese_bug_substantial():
    with patch(
        "zd_agent.trigger.decider._call_llm",
        AsyncMock(return_value="not json at all"),
    ):
        v = await classify_messages(
            messages=["为什么有些 swagger api 工具调用，并发调用会丢失鉴权信息，严重 bug"],
            model="gpt-5.4-mini",
            api_key="x",
        )
    assert v.verdict == "substantial_issue"
    assert v.completeness == 65
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
async def test_all_customer_messages_sent_to_llm():
    many = [f"msg {i}" for i in range(10)]
    captured_content = []

    async def fake_llm(*, user_content, model, api_key, base_url=None, timeout=60.0):
        captured_content.append(user_content)
        return '{"verdict": "greeting", "completeness": 20, "reason": "x"}'

    with patch("zd_agent.trigger.decider._call_llm", fake_llm):
        await classify_messages(messages=many, model="gpt-5.4-mini", api_key="x")
    assert captured_content
    # 文本调用，user_content 应该是 str
    text = captured_content[0]
    assert isinstance(text, str)
    # 全部客户消息都应进入判定上下文，不能只看最近 5 条。
    for i in range(10):
        assert f"msg {i}" in text


@pytest.mark.asyncio
async def test_vision_request_shape_with_images():
    """有 image_data_urls 时，user_content 应是多模态 parts 列表。"""
    captured = []

    async def fake_llm(*, user_content, model, api_key, base_url=None, timeout=60.0):
        captured.append(user_content)
        return (
            '{"verdict": "substantial_issue", "completeness": 75, '
            '"reason": "看到截图里有 500 报错"}'
        )

    with patch("zd_agent.trigger.decider._call_llm", fake_llm):
        v = await classify_messages(
            messages=["看下截图"],
            model="gpt-5.4-mini",
            api_key="x",
            enable_vision_input=True,
            image_data_urls=[
                "data:image/png;base64,AAAA",
                "data:image/jpeg;base64,BBBB",
            ],
        )
    assert v.verdict == "substantial_issue"
    assert v.completeness == 75
    assert captured
    parts = captured[0]
    assert isinstance(parts, list)
    # 第一个是 text，其余是 image_url
    assert parts[0]["type"] == "text"
    assert "看下截图" in parts[0]["text"]
    image_parts = [p for p in parts if p["type"] == "image_url"]
    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert image_parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_vision_with_images_no_text_messages():
    """客户只发图片没有文字也能跑——常见的 #2260 情形。"""
    async def fake_llm(*, user_content, model, api_key, base_url=None, timeout=60.0):
        # 仍是多模态 parts
        assert isinstance(user_content, list)
        return (
            '{"verdict": "substantial_issue", "completeness": 70, '
            '"reason": "截图里看到 workflow 报错"}'
        )

    with patch("zd_agent.trigger.decider._call_llm", fake_llm):
        v = await classify_messages(
            messages=["[客户附图]"],
            model="gpt-5.4-mini",
            api_key="x",
            enable_vision_input=True,
            image_data_urls=["data:image/png;base64,AAAA"],
        )
    assert v.verdict == "substantial_issue"


@pytest.mark.asyncio
async def test_vision_disabled_skips_image_parts_for_any_model():
    captured = []

    async def fake_llm(*, user_content, model, api_key, base_url=None, timeout=60.0):
        captured.append(user_content)
        return (
            '{"verdict": "insufficient_info", "completeness": 50, '
            '"reason": "模型不可看图，需客户补充文字说明"}'
        )

    with patch("zd_agent.trigger.decider._call_llm", fake_llm):
        v = await classify_messages(
            messages=["看下截图"],
            model="any-text-only-model",
            api_key="x",
            image_data_urls=["data:image/png;base64,AAAA"],
        )

    assert v.verdict == "insufficient_info"
    assert captured
    prompt = captured[0]
    assert isinstance(prompt, str)
    assert "关闭了触发判定视觉输入" in prompt
    assert "data:image/png" not in prompt


@pytest.mark.asyncio
async def test_vision_disabled_uses_image_count_without_downloaded_data():
    captured = []

    async def fake_llm(*, user_content, model, api_key, base_url=None, timeout=60.0):
        captured.append(user_content)
        return (
            '{"verdict": "insufficient_info", "completeness": 50, '
            '"reason": "只有截图事实，没有文字问题"}'
        )

    with patch("zd_agent.trigger.decider._call_llm", fake_llm):
        v = await classify_messages(
            messages=[],
            model="text-only-model",
            api_key="x",
            image_count=2,
            image_data_urls=[],
        )

    assert v.verdict == "insufficient_info"
    assert captured
    prompt = captured[0]
    assert isinstance(prompt, str)
    assert "客户还附了 2 张截图" in prompt


@pytest.mark.asyncio
async def test_vision_enabled_reports_unavailable_images_without_claiming_disabled():
    captured = []

    async def fake_llm(*, user_content, model, api_key, base_url=None, timeout=60.0):
        captured.append(user_content)
        return (
            '{"verdict": "insufficient_info", "completeness": 50, '
            '"reason": "截图不可用，需要文字说明"}'
        )

    with patch("zd_agent.trigger.decider._call_llm", fake_llm):
        verdict = await classify_messages(
            messages=["see screenshot"],
            model="vision-model",
            api_key="x",
            image_count=1,
            image_data_urls=[],
            enable_vision_input=True,
        )

    assert verdict.verdict == "insufficient_info"
    prompt = captured[0]
    assert isinstance(prompt, str)
    assert "视觉输入已开启，但截图内容未能加载" in prompt
    assert "关闭了触发判定视觉输入" not in prompt


# ============================================================================
# M2 新增：3 个新 verdict 类型 + high_risk_category + fast-path
# ============================================================================

class TestFastPathRequestHuman:
    """Fast-path：精确匹配 human keyword 不走 LLM，省 cost + 提速。"""

    @pytest.mark.parametrize("last_msg", [
        "human",
        "Human",
        "HUMAN",
        "  human  ",
        "human.",
        "human!",
        "人工",
        "找人工",
        "转人工",
        "需要人工",
        "真人",
        "找真人",
        "live agent",
        "Live Agent",
        "real person",
        "speak to human",
        "talk to human",
    ])
    @pytest.mark.asyncio
    async def test_fast_path_matches_exact_keyword(self, last_msg: str) -> None:
        """精确等于已知 keyword（去空格 + 标点 + 大小写不敏感）→ request_human"""
        # 用 AsyncMock 兜底，但应该不会调到（fast-path 短路）
        with patch(
            "zd_agent.trigger.decider._call_llm",
            AsyncMock(side_effect=AssertionError("LLM 不应被调用")),
        ):
            v = await classify_messages(
                messages=["earlier context", last_msg],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.verdict == "request_human"
        assert v.completeness == 100

    @pytest.mark.asyncio
    async def test_fast_path_not_triggered_on_partial_match(self) -> None:
        """部分匹配不触发 fast-path（"the human resources page is broken" 不能误判）"""
        llm_resp = (
            '{"verdict": "substantial_issue", "completeness": 75, '
            '"reason": "HR 页面挂了", "high_risk_category": null}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["the human resources page is broken"],
                model="gpt-5.4-mini", api_key="x",
            )
        # LLM 被调到，且没误判 request_human
        assert v.verdict == "substantial_issue"

    @pytest.mark.asyncio
    async def test_fast_path_checks_last_message_only(self) -> None:
        """只看最后一条消息（之前提到 'human' 不算请求）"""
        llm_resp = (
            '{"verdict": "substantial_issue", "completeness": 80, '
            '"reason": "x", "high_risk_category": null}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["I'm not a human... I mean, I am a person. Anyway, my workflow broke."],
                model="gpt-5.4-mini", api_key="x",
            )
        # 最后一条不是精确 "human"，走 LLM
        assert v.verdict == "substantial_issue"


class TestVerdictRequestHumanFromLLM:
    """LLM-judged request_human：客户用变体表达想转人工。"""

    @pytest.mark.asyncio
    async def test_llm_classifies_request_human(self) -> None:
        llm_resp = (
            '{"verdict": "request_human", "completeness": 90, '
            '"reason": "客户要求转给真人客服", "high_risk_category": null}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["Can someone real please help me, this bot doesn't understand"],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.verdict == "request_human"
        assert v.completeness == 90


class TestVerdictSelfResolved:
    @pytest.mark.asyncio
    async def test_self_resolved(self) -> None:
        llm_resp = (
            '{"verdict": "self_resolved", "completeness": 85, '
            '"reason": "客户自己搞定了", "high_risk_category": null}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["Never mind, I figured it out — thanks!"],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.verdict == "self_resolved"


class TestHighRiskCategory:
    @pytest.mark.asyncio
    async def test_refund_category(self) -> None:
        llm_resp = (
            '{"verdict": "substantial_issue", "completeness": 80, '
            '"reason": "退款诉求", "high_risk_category": "refund"}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["I want a refund for the $200 charge last month"],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.verdict == "substantial_issue"
        assert v.high_risk_category == "refund"
        assert v.high_risk_reason == "退款诉求"

    @pytest.mark.asyncio
    async def test_emotional_category(self) -> None:
        llm_resp = (
            '{"verdict": "substantial_issue", "completeness": 70, '
            '"reason": "客户带强烈情绪", "high_risk_category": "emotional"}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["THIS IS UNACCEPTABLE!!! Your product is broken AGAIN!"],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.high_risk_category == "emotional"

    @pytest.mark.asyncio
    async def test_incident_category(self) -> None:
        llm_resp = (
            '{"verdict": "substantial_issue", "completeness": 85, '
            '"reason": "疑似平台级故障", "high_risk_category": "incident"}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["Our entire team can't log in right now, is your service down?"],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.high_risk_category == "incident"

    @pytest.mark.asyncio
    async def test_no_high_risk_default(self) -> None:
        """没标 high_risk → None（不是空字符串、不是 "null" 字符串）"""
        llm_resp = (
            '{"verdict": "substantial_issue", "completeness": 80, '
            '"reason": "正常工单", "high_risk_category": null}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["workflow returns 500"],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.high_risk_category is None
        assert v.high_risk_reason == ""

    @pytest.mark.asyncio
    async def test_high_risk_string_null_treated_as_none(self) -> None:
        """LLM 误把 null 写成字符串 "null" / "none" → 解析为 None 不报错"""
        llm_resp = (
            '{"verdict": "substantial_issue", "completeness": 80, '
            '"reason": "x", "high_risk_category": "null"}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["x"],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.high_risk_category is None

    @pytest.mark.asyncio
    async def test_unknown_high_risk_falls_back_to_none(self) -> None:
        """LLM 编了未支持的 category → 当 None 处理（保守）"""
        llm_resp = (
            '{"verdict": "substantial_issue", "completeness": 80, '
            '"reason": "x", "high_risk_category": "bogus_category"}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["x"],
                model="gpt-5.4-mini", api_key="x",
            )
        assert v.high_risk_category is None


class TestBackwardCompatibility:
    """确保现有 3 个 verdict 类型仍然正常工作。"""

    @pytest.mark.asyncio
    async def test_greeting_unchanged(self) -> None:
        llm_resp = (
            '{"verdict": "greeting", "completeness": 10, '
            '"reason": "x", "high_risk_category": null}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["hello"], model="gpt-5.4-mini", api_key="x",
            )
        assert v.verdict == "greeting"
        assert v.high_risk_category is None

    @pytest.mark.asyncio
    async def test_insufficient_unchanged(self) -> None:
        llm_resp = (
            '{"verdict": "insufficient_info", "completeness": 40, '
            '"reason": "x", "high_risk_category": null}'
        )
        with patch(
            "zd_agent.trigger.decider._call_llm", AsyncMock(return_value=llm_resp),
        ):
            v = await classify_messages(
                messages=["my app is broken"], model="gpt-5.4-mini", api_key="x",
            )
        assert v.verdict == "insufficient_info"
