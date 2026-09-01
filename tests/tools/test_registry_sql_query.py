"""Raw read-only SQL and SigNoz queries remain available to casework."""

from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry


def test_raw_sql_query_is_registered_without_mandatory_tenant_scope():
    reg = create_casework_tool_registry()
    tool = reg.get("sql_query")
    assert tool is not None
    assert tool.parameters["required"] == ["database", "sql"]


def test_raw_signoz_query_is_registered_without_mandatory_tenant_scope():
    reg = create_casework_tool_registry()
    tool = reg.get("signoz_query")
    assert tool is not None
    assert tool.parameters["required"] == ["sql"]
