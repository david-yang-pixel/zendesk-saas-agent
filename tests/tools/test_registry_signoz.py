"""Registry exposes only tenant-scoped SigNoz capabilities."""

from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry


def test_signoz_tools_registered():
    reg = create_casework_tool_registry()
    for name in [
        "saas_deployed_version",
        "signoz_errors",
        "signoz_traceback",
        "signoz_search_logs",
        "signoz_aggregate_logs",
        "signoz_search_traces",
        "signoz_aggregate_traces",
    ]:
        assert name in reg, f"{name} 未注册"


def test_structured_signoz_tools_have_remote_schemas():
    reg = create_casework_tool_registry()
    search_logs = reg.get("signoz_search_logs")
    assert search_logs is not None
    assert "timeRange" in search_logs.parameters["properties"]
    assert "tenant" in search_logs.parameters["properties"]

    search_traces = reg.get("signoz_search_traces")
    assert search_traces is not None
    assert "tenant" in search_traces.parameters["properties"]


def test_global_and_trace_only_signoz_capabilities_are_not_registered():
    reg = create_casework_tool_registry()

    for name in (
        "signoz_services",
        "signoz_list_services",
        "signoz_get_trace_details",
        "signoz_list_metrics",
        "signoz_get_field_keys",
        "signoz_get_field_values",
        "signoz_query_metrics",
    ):
        assert reg.get(name) is None


def test_raw_signoz_query_is_registered_only_as_tenant_scoped_wrapper():
    reg = create_casework_tool_registry()

    tool = reg.get("signoz_query")
    assert tool is not None
    assert getattr(tool, "casework_scope_enforced", False) is True
    assert tool.parameters["required"] == ["tenant", "sql"]
