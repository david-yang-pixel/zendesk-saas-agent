from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry


def test_casework_registry_exposes_only_approved_read_capabilities() -> None:
    registry = create_casework_tool_registry()
    names = {tool.name for tool in registry.list_tools()}

    assert {
        "inspector_exec",
        "sql_query",
        "official_docs_search",
        "github_get_pr",
        "signoz_query",
        "zendesk_get_ticket",
    } <= names
    assert {
        "zendesk_public_reply",
        "zendesk_internal_reply",
        "zendesk_add_tags",
        "feishu_push",
        "run_script",
        "sandbox_exec",
        "run_subagent",
        "run_subagents_parallel",
        "saas_branch_switch",
        "custom_mcp_call",
    }.isdisjoint(names)
    assert all(not tool.is_write_operation for tool in registry.list_tools())
    assert all(not tool.requires_sandbox for tool in registry.list_tools())
