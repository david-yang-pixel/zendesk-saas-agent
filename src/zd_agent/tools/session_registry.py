from __future__ import annotations

from collections.abc import Iterable

from zd_agent.tools.base import Tool, ToolRegistry


def build_session_tool_registry(
    base: ToolRegistry,
    *,
    extensions: Iterable[Tool] = (),
    root: bool,
) -> ToolRegistry:
    """Build a frozen registry view for one root or depth-one child loop."""

    registry = ToolRegistry()
    for tool in (*base.list_tools(), *tuple(extensions)):
        if tool.root_only and not root:
            continue
        registry.register(tool)
    return registry
