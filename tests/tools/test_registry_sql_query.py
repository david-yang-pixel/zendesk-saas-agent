"""Test SqlQuery is registered in the tool registry."""
from zd_agent.tools.registry import create_tool_registry


def test_sql_query_registered():
    reg = create_tool_registry()
    tool = reg.get("sql_query")
    assert tool is not None
    assert tool.name == "sql_query"
    assert tool.group == "database_query"


def test_sql_query_openai_schema_includes_database_enum():
    reg = create_tool_registry()
    schema = reg.get("sql_query").to_openai_schema()
    assert "DB Gateway MCP" in schema["function"]["description"]
    params = schema["function"]["parameters"]
    assert "database" in params["properties"]
    assert params["properties"]["database"]["enum"] == ["prod", "billing", "plugin"]
    assert params["required"] == ["database", "sql"]
