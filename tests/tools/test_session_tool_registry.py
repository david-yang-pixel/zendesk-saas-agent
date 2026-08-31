from __future__ import annotations

from typing import Any

from zd_agent.tools.base import Tool, ToolRegistry, ToolResult
from zd_agent.tools.session_registry import build_session_tool_registry


class NamedTool(Tool):
    description = "test"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, name: str, *, root_only: bool = False) -> None:
        self.name = name
        self.root_only = root_only

    async def execute(self, args: dict[str, Any], session) -> ToolResult:
        _ = args, session
        return ToolResult(output="ok")


def test_root_registry_includes_control_tools_and_child_registry_excludes_them() -> None:
    base = ToolRegistry()
    base.register(NamedTool("zendesk_get_ticket"))
    extensions = (
        NamedTool("memory_search"),
        NamedTool("subagent_spawn", root_only=True),
    )

    root = build_session_tool_registry(base, extensions=extensions, root=True)
    child = build_session_tool_registry(base, extensions=extensions, root=False)

    assert {tool.name for tool in root.list_tools()} == {
        "zendesk_get_ticket",
        "memory_search",
        "subagent_spawn",
    }
    assert {tool.name for tool in child.list_tools()} == {
        "zendesk_get_ticket",
        "memory_search",
    }
