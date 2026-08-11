"""Raw SQL is intentionally unreachable from the v2 model tool surface."""

from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry


def test_raw_sql_query_is_not_registered():
    reg = create_casework_tool_registry()
    assert reg.get("sql_query") is None


def test_scoped_inspector_replaces_model_authored_sql():
    reg = create_casework_tool_registry()
    inspector = reg.get("inspector_exec")
    assert inspector is not None
    assert "tenant_id" in inspector.parameters["properties"]
