"""Tests for prompts.public_reply.templates (M6).

覆盖：
- 5 个静态模板（A silent / B greeting / D retry / E handoff / G closed）
  - 客户名字插入
  - 客户名字为空时去掉空格
  - 不含 footer（footer 在工具层加）
  - 不问 Workspace ID（绝不要再向客户索取已知字段）
  - 一律英文（无中文字符）
- 模板 C insufficient_info：
  - 调 LLM 生成动态 bullets
  - LLM 错误 fallback 到 3 个硬编码 bullet
  - LLM 返回不规范（缺 "- " 前缀）被自动 normalize
  - LLM 返回过多（>3）截断到 3
  - LLM 返回空 / 全空 → fallback
- build_ask_body 分发：
  - ask_count=0 + verdict ∈ {no_message/greeting/insufficient_info} → A/B/C
    语义，但正文优先由 LLM 生成，失败时才 fallback
  - ask_count>=1 一律 → D（retry）语义，同样优先由 LLM 生成
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.prompts.public_reply.templates import (
    _generate_ask_body_with_llm,
    _generate_missing_info_bullets,
    build_ask_body,
    closed_template,
    greeting_template,
    handoff_template,
    insufficient_info_template,
    retry_template,
    silent_template,
)


# ---------------------------------------------------------------------------
# Static templates A / B / D / E / G
# ---------------------------------------------------------------------------

class TestStaticTemplates:
    @pytest.mark.parametrize("fn", [
        silent_template, greeting_template, retry_template,
        handoff_template, closed_template,
    ])
    def test_no_footer_in_body(self, fn) -> None:
        """模板正文不含 footer（footer 由 ZendeskPublicReply 工具层硬加）"""
        body = fn(customer_name="Alice")
        assert "🤖" not in body
        assert "Dify SaaS Agent" not in body
        assert '"human"' not in body
        assert "---" not in body

    @pytest.mark.parametrize("fn", [
        silent_template, greeting_template, retry_template,
        handoff_template, closed_template,
    ])
    def test_no_workspace_id_ask(self, fn) -> None:
        """绝不向客户索取 Workspace ID / Email / Name（已知字段）"""
        body = fn(customer_name="Bob").lower()
        assert "workspace id" not in body
        assert "your email" not in body

    @pytest.mark.parametrize("fn", [
        silent_template, greeting_template, retry_template,
        handoff_template, closed_template,
    ])
    def test_no_chinese_chars(self, fn) -> None:
        """messaging 工单一律英文回复"""
        body = fn(customer_name="Carol")
        # 检查是否有任何 CJK 字符
        has_chinese = any("一" <= c <= "鿿" for c in body)
        assert not has_chinese, f"Found Chinese char in: {body!r}"

    def test_silent_template_basic(self) -> None:
        body = silent_template(customer_name="Alice")
        assert "Alice" in body
        assert "ticket" in body.lower()
        assert "describe" in body.lower()

    def test_silent_template_no_name(self) -> None:
        """没拿到客户名 → 不留奇怪空格"""
        body = silent_template(customer_name="")
        assert "Hi!" in body
        assert "Hi !" not in body  # 不该有 "Hi  !"

    def test_greeting_template_basic(self) -> None:
        body = greeting_template(customer_name="Dave")
        assert "Dave" in body
        assert "what" in body.lower() or "help" in body.lower()

    def test_retry_template_has_examples(self) -> None:
        """模板 D 必须有具体例子帮客户表达"""
        body = retry_template(customer_name="Eve")
        # 至少含一个示例引号
        assert '"' in body
        # 应该有 500 / app / workflow 等典型示例
        body_lower = body.lower()
        assert any(kw in body_lower for kw in ("500", "workflow", "app"))

    def test_handoff_template_mentions_human(self) -> None:
        """转人工通知必须明示 'human teammate'"""
        body = handoff_template(customer_name="Frank")
        assert "human" in body.lower()
        assert "Frank" in body

    def test_handoff_no_name(self) -> None:
        body = handoff_template(customer_name="")
        assert "Hi!" in body or "Hi," in body
        # 不应有 "Hi ," 这种空格
        assert "Hi ," not in body
        assert "Hi  " not in body

    def test_closed_template_offers_reopen(self) -> None:
        """模板 G 必须给客户重启入口"""
        body = closed_template(customer_name="Grace")
        body_lower = body.lower()
        assert "reply" in body_lower or "new ticket" in body_lower


# ---------------------------------------------------------------------------
# Dynamic template C (insufficient_info)
# ---------------------------------------------------------------------------

class TestMissingInfoBullets:
    """_generate_missing_info_bullets — LLM 生成 + 各种 fallback"""

    @pytest.mark.asyncio
    async def test_llm_returns_three_bullets(self) -> None:
        """LLM 返回 3 个 bullet 应被原样使用"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content":
                '{"bullets": ['
                '"- The specific app id",'
                '"- The exact error message",'
                '"- When this started"]}'
            }}],
        })
        async_post = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient.post", async_post):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["my app is broken"],
                model="m", api_key="k",
            )
        assert len(bullets) == 3
        assert bullets[0] == "- The specific app id"

    @pytest.mark.asyncio
    async def test_llm_returns_excess_bullets_truncated(self) -> None:
        """LLM 给 5 个 → 截到 3"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content":
                '{"bullets": ["- a", "- b", "- c", "- d", "- e"]}'
            }}],
        })
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        assert len(bullets) == 3

    @pytest.mark.asyncio
    async def test_llm_missing_dash_prefix_normalized(self) -> None:
        """LLM 漏了 '- ' 前缀 → 自动补上"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content":
                '{"bullets": ["the app id", "the error", "when it started"]}'
            }}],
        })
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        assert all(b.startswith("- ") for b in bullets)

    @pytest.mark.asyncio
    async def test_llm_empty_bullets_fallback(self) -> None:
        """LLM 返回空 bullets → fallback 3 个通用 bullet"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"bullets": []}'}}],
        })
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        assert len(bullets) == 3  # fallback
        assert all(b.startswith("- ") for b in bullets)

    @pytest.mark.asyncio
    async def test_llm_http_error_fallback(self) -> None:
        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(side_effect=RuntimeError("net err")),
        ):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        assert len(bullets) == 3

    @pytest.mark.asyncio
    async def test_llm_non_200_fallback(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "server err"
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        assert len(bullets) == 3

    @pytest.mark.asyncio
    async def test_empty_customer_messages_uses_fallback(self) -> None:
        """没客户消息时直接返回 fallback，不调 LLM"""
        with patch("httpx.AsyncClient.post", AsyncMock()) as mock_post:
            bullets = await _generate_missing_info_bullets(
                customer_messages=[], model="m", api_key="k",
            )
        assert len(bullets) == 3
        mock_post.assert_not_called()

    # ── post-filter：LLM 不听话问了已知字段（workspace id / email / name）──

    @pytest.mark.asyncio
    async def test_llm_asks_workspace_id_filtered_out(self) -> None:
        """LLM 生成的 bullet 含 'Workspace ID' → 过滤掉，剩 2 个仍可用"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content":
                '{"bullets": ['
                '"- Your Workspace ID",'          # 必须被过滤
                '"- The error message text",'
                '"- When this started"]}'
            }}],
        })
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        # workspace id 那条被过滤掉
        assert len(bullets) == 2
        for b in bullets:
            assert "workspace id" not in b.lower()

    @pytest.mark.asyncio
    async def test_llm_asks_email_filtered_out(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content":
                '{"bullets": ['
                '"- Your email address",'
                '"- A screenshot of the error",'
                '"- The app ID"]}'
            }}],
        })
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        assert len(bullets) == 2
        for b in bullets:
            low = b.lower()
            assert "email" not in low or "address" not in low

    @pytest.mark.asyncio
    async def test_llm_asks_all_forbidden_falls_back(self) -> None:
        """LLM 全部生成已知字段问题 → 过滤后 < 2 条 → fallback 3 个通用"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content":
                '{"bullets": ['
                '"- Your Workspace ID",'
                '"- Your email",'
                '"- Your name"]}'
            }}],
        })
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        # 全被过滤 → fallback
        assert len(bullets) == 3
        all_text = " ".join(bullets).lower()
        assert "workspace id" not in all_text
        assert "your email" not in all_text
        assert "your name" not in all_text

    @pytest.mark.asyncio
    async def test_llm_asks_one_forbidden_two_kept(self) -> None:
        """1 个被过滤 + 2 个保留 → 不 fallback（≥2 条 OK）"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content":
                '{"bullets": ['
                '"- Full name of the affected app",'  # ← "full name" 触发过滤
                '"- Error message text",'
                '"- When this started"]}'
            }}],
        })
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            bullets = await _generate_missing_info_bullets(
                customer_messages=["x"], model="m", api_key="k",
            )
        # 第一条命中 "full name" 被过滤，剩 2 个
        assert len(bullets) == 2


class TestInsufficientInfoTemplate:
    @pytest.mark.asyncio
    async def test_full_template_with_bullets(self) -> None:
        with patch(
            "zd_agent.prompts.public_reply.templates._generate_missing_info_bullets",
            AsyncMock(return_value=[
                "- The exact error message",
                "- The app id",
            ]),
        ):
            body = await insufficient_info_template(
                customer_name="Alice",
                customer_messages=["my app broken"],
                model="m", api_key="k",
            )
        assert "Alice" in body
        assert "- The exact error message" in body
        assert "- The app id" in body
        # 不含 footer
        assert "🤖" not in body

    @pytest.mark.asyncio
    async def test_no_name_template(self) -> None:
        with patch(
            "zd_agent.prompts.public_reply.templates._generate_missing_info_bullets",
            AsyncMock(return_value=["- x", "- y"]),
        ):
            body = await insufficient_info_template(
                customer_name="",
                customer_messages=["x"],
                model="m", api_key="k",
            )
        # 没名字时也不该有奇怪的逗号 + 空格组合
        assert not body.startswith(",")


# ---------------------------------------------------------------------------
# build_ask_body dispatcher
# ---------------------------------------------------------------------------

class TestBuildAskBody:
    @pytest.mark.asyncio
    async def test_no_message_ask_count_zero_uses_llm_body_with_A_semantics(self) -> None:
        with patch(
            "zd_agent.prompts.public_reply.templates._generate_ask_body_with_llm",
            AsyncMock(return_value="Alice, what issue should we investigate?"),
        ) as mock_generate:
            body, name = await build_ask_body(
                verdict_kind="no_message", ask_count=0,
                customer_name="Alice", customer_messages=[],
                model="m", api_key="k",
            )
        assert name == "A"
        assert body == "Alice, what issue should we investigate?"
        assert mock_generate.await_args.kwargs["verdict_kind"] == "no_message"

    @pytest.mark.asyncio
    async def test_greeting_ask_count_zero_uses_llm_body_with_B_semantics(self) -> None:
        with patch(
            "zd_agent.prompts.public_reply.templates._generate_ask_body_with_llm",
            AsyncMock(return_value="Bob, what can I help you with?"),
        ) as mock_generate:
            body, name = await build_ask_body(
                verdict_kind="greeting", ask_count=0,
                customer_name="Bob", customer_messages=["hi"],
                model="m", api_key="k",
            )
        assert name == "B"
        assert body == "Bob, what can I help you with?"
        assert mock_generate.await_args.kwargs["verdict_kind"] == "greeting"

    @pytest.mark.asyncio
    async def test_insufficient_info_ask_count_zero_uses_llm_body_with_C_semantics(self) -> None:
        with patch(
            "zd_agent.prompts.public_reply.templates._generate_ask_body_with_llm",
            AsyncMock(return_value="Carol, please send the exact error and app ID."),
        ) as mock_generate:
            body, name = await build_ask_body(
                verdict_kind="insufficient_info", ask_count=0,
                customer_name="Carol",
                customer_messages=["it's broken"],
                model="m", api_key="k",
            )
        assert name == "C"
        assert body == "Carol, please send the exact error and app ID."
        assert mock_generate.await_args.kwargs["verdict_kind"] == "insufficient_info"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verdict_kind", [
        "no_message", "greeting", "insufficient_info",
    ])
    @pytest.mark.parametrize("ask_count", [1, 2, 5, 9])
    async def test_ask_count_ge_one_always_returns_D(
        self, verdict_kind: str, ask_count: int,
    ) -> None:
        with patch(
            "zd_agent.prompts.public_reply.templates._generate_ask_body_with_llm",
            AsyncMock(return_value="Dave, I still need the exact error."),
        ) as mock_generate:
            body, name = await build_ask_body(
                verdict_kind=verdict_kind, ask_count=ask_count,
                customer_name="Dave", customer_messages=["x"],
                model="m", api_key="k",
            )
        assert name == "D"
        assert body == "Dave, I still need the exact error."
        assert mock_generate.await_args.kwargs["ask_count"] == ask_count

    @pytest.mark.asyncio
    async def test_build_ask_body_falls_back_when_llm_body_missing(self) -> None:
        with patch(
            "zd_agent.prompts.public_reply.templates._generate_ask_body_with_llm",
            AsyncMock(return_value=None),
        ):
            body, name = await build_ask_body(
                verdict_kind="insufficient_info", ask_count=0,
                customer_name="Carol",
                customer_messages=["it's broken"],
                model="m", api_key="k",
            )
        assert name == "C"
        assert "To investigate faster" in body
        assert "exact error" in body.lower()


class TestAskBodyGeneration:
    @pytest.mark.asyncio
    async def test_llm_generated_ask_body_is_used(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{
                "message": {
                    "content": (
                        '{"body": "Alice, please send the exact error text and '
                        'which workflow is affected."}'
                    ),
                },
            }],
        })

        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            body = await _generate_ask_body_with_llm(
                verdict_kind="insufficient_info",
                ask_count=0,
                customer_name="Alice",
                customer_messages=["workflow broken"],
                model="m",
                api_key="k",
            )

        assert body == (
            "Alice, please send the exact error text and which workflow is affected."
        )

    @pytest.mark.asyncio
    async def test_llm_generated_ask_body_rejects_known_field_requests(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"body": "Please share your Workspace ID."}',
                },
            }],
        })

        with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            body = await _generate_ask_body_with_llm(
                verdict_kind="insufficient_info",
                ask_count=0,
                customer_name="Alice",
                customer_messages=["app broken"],
                model="m",
                api_key="k",
            )

        assert body is None
