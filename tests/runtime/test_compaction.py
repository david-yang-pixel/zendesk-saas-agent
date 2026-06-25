from zd_agent.runtime.compaction import history_snip


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
