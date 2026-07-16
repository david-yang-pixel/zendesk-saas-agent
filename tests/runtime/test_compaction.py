from unittest.mock import AsyncMock

import pytest

from zd_agent.runtime.compaction import compact_messages, count_tokens, history_snip
from zd_agent.runtime.llm.types import LLMResponse


def test_count_tokens_includes_preserved_responses_reasoning_tokens() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "_zd_responses_reasoning_tokens": 1200,
            "_zd_responses_output": [{"type": "reasoning"}],
        }
    ]

    assert count_tokens(messages) >= 1204


def test_history_snip_preserves_each_subagent_block_head() -> None:
    content = "\n\n".join([
        "[子 Agent 1] 结论 A\n" + ("a" * 1000),
        "[子 Agent 2] 结论 B\n" + ("b" * 1000),
        "[子 Agent 3] 结论 C\n" + ("c" * 1000),
    ])
    messages = [
        {
            "role": "tool",
            "tool_call_id": "call-subagents",
            "name": "run_subagents_parallel",
            "content": content,
        }
    ]

    snipped = history_snip(messages)

    output = snipped[0]["content"]
    assert "[子 Agent 1] 结论 A" in output
    assert "[子 Agent 2] 结论 B" in output
    assert "[子 Agent 3] 结论 C" in output
    assert len(output) < len(content)


def test_history_snip_still_snips_regular_tool_output() -> None:
    messages = [
        {
            "role": "tool",
            "tool_call_id": "call-grep",
            "name": "git_grep",
            "content": "x" * 1000,
        }
    ]

    snipped = history_snip(messages)

    assert len(snipped[0]["content"]) < 300
    assert "[... 输出已精简 ...]" in snipped[0]["content"]


@pytest.mark.asyncio
async def test_level2_compaction_restarts_responses_history_from_clean_summary() -> None:
    messages = [
        {"role": "system", "content": "support system"},
        {"role": "user", "content": "ticket details"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
            "_zd_responses_output": [
                {"type": "reasoning", "encrypted_content": "secret"},
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "lookup",
                    "arguments": "{}",
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "lookup",
            "content": "result",
        },
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": "working"},
        {"role": "user", "content": "more"},
        {"role": "assistant", "content": "still working"},
        {"role": "user", "content": "final"},
    ]
    llm = AsyncMock()
    llm.chat.return_value = LLMResponse(
        content="clean summary",
        tool_calls=[],
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=3,
    )

    compacted = await compact_messages(messages, llm)

    assert compacted == [
        {"role": "system", "content": "support system"},
        {
            "role": "user",
            "content": "[以下是之前分析过程的摘要]\n\nclean summary",
        },
    ]
    assert all("_zd_responses_output" not in message for message in compacted)
