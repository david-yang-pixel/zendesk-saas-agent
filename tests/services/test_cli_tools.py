class FakeTool:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.parameters = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        }

    def to_openai_schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class FakeRegistry:
    def __init__(self) -> None:
        self._tools = [
            FakeTool("zendesk_search", "Search tickets"),
            FakeTool("knowledge_read", "Read knowledge"),
        ]

    def list_tools(self) -> list[FakeTool]:
        return self._tools

    def get(self, name: str) -> FakeTool | None:
        return next((tool for tool in self._tools if tool.name == name), None)

    def __len__(self) -> int:
        return len(self._tools)


def test_collect_tool_list_returns_summary_rows():
    from zd_agent.services.cli_tools import collect_tool_list

    result = collect_tool_list(registry_factory=FakeRegistry)

    assert result.total == 2
    assert result.rows == [
        {"index": 1, "name": "zendesk_search", "description": "Search tickets"},
        {"index": 2, "name": "knowledge_read", "description": "Read knowledge"},
    ]


def test_get_tool_schema_returns_schema_for_existing_tool():
    from zd_agent.services.cli_tools import get_tool_schema

    result = get_tool_schema("zendesk_search", registry_factory=FakeRegistry)

    assert result.found is True
    assert result.schema == {
        "type": "function",
        "function": {
            "name": "zendesk_search",
            "description": "Search tickets",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    }


def test_get_tool_schema_returns_not_found_for_missing_tool():
    from zd_agent.services.cli_tools import get_tool_schema

    result = get_tool_schema("missing_tool", registry_factory=FakeRegistry)

    assert result.found is False
    assert result.schema is None
