"""Raw SQL remains available through the tenant-scoped v2 wrapper."""

from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry


def test_raw_sql_query_is_registered_with_mandatory_tenant_scope():
    reg = create_casework_tool_registry()
    tool = reg.get("sql_query")
    assert tool is not None
    assert tool.parameters["required"] == ["database", "tenant_id", "sql"]


def test_scoped_signoz_query_is_registered_with_mandatory_tenant_scope():
    reg = create_casework_tool_registry()
    tool = reg.get("signoz_query")
    assert tool is not None
    assert tool.parameters["required"] == ["tenant", "sql"]
