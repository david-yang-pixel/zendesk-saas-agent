"""Registry exposes both structured SigNoz tools and focused sz helpers."""

from zd_agent.tools.registry import create_tool_registry


def test_signoz_tools_registered():
    reg = create_tool_registry()
    for name in [
        "saas_deployed_version",
        "signoz_errors",
        "signoz_traceback",
        "signoz_services",
        "signoz_query",
        "signoz_list_services",
        "signoz_get_trace_details",
        "signoz_list_metrics",
        "signoz_get_field_keys",
        "signoz_get_field_values",
        "signoz_search_logs",
        "signoz_aggregate_logs",
        "signoz_search_traces",
        "signoz_aggregate_traces",
        "signoz_query_metrics",
    ]:
        assert name in reg, f"{name} 未注册"


def test_structured_signoz_tools_have_remote_schemas():
    reg = create_tool_registry()
    search_logs = reg.get("signoz_search_logs")
    assert search_logs is not None
    assert "timeRange" in search_logs.parameters["properties"]
    assert "tenant" in search_logs.parameters["properties"]

    trace_details = reg.get("signoz_get_trace_details")
    assert trace_details is not None
    assert trace_details.parameters["required"] == ["traceId"]
