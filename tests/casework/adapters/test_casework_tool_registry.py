from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry


def test_casework_registry_exposes_only_approved_read_capabilities() -> None:
    registry = create_casework_tool_registry()
    names = {tool.name for tool in registry.list_tools()}

    assert {
        "document_indexing_status",
        "inspector_exec",
        "official_docs_search",
        "signoz_query",
        "github_get_pr",
        "signoz_search_logs",
        "sql_query",
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
        "zendesk_get_attachments",
        "zendesk_get_user",
        "zendesk_search",
        "ticket_record_search",
        "signoz_services",
        "signoz_list_services",
        "signoz_get_trace_details",
        "signoz_list_metrics",
        "signoz_get_field_keys",
        "signoz_get_field_values",
        "signoz_query_metrics",
    }.isdisjoint(names)
    assert all(not tool.is_write_operation for tool in registry.list_tools())
    assert all(not tool.requires_sandbox for tool in registry.list_tools())
