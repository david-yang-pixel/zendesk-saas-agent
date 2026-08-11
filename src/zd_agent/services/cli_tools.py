"""Tool registry inspection helpers for CLI commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class _CliTool(Protocol):
    name: str
    description: str

    def to_openai_schema(self) -> dict[str, Any]: ...


class _CliToolRegistry(Protocol):
    def list_tools(self) -> list[_CliTool]: ...

    def get(self, name: str) -> _CliTool | None: ...

    def __len__(self) -> int: ...


RegistryFactory = Callable[[], _CliToolRegistry]


@dataclass(frozen=True)
class ToolListResult:
    total: int
    rows: list[dict[str, object]]


@dataclass(frozen=True)
class ToolSchemaResult:
    found: bool
    schema: dict[str, Any] | None


def _default_registry_factory() -> _CliToolRegistry:
    from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry

    return create_casework_tool_registry()


def collect_tool_list(
    *,
    registry_factory: RegistryFactory = _default_registry_factory,
) -> ToolListResult:
    """Collect tool summary rows for the CLI tool list command."""
    registry = registry_factory()
    rows = [
        {
            "index": index,
            "name": tool.name,
            "description": tool.description,
        }
        for index, tool in enumerate(registry.list_tools(), 1)
    ]
    return ToolListResult(total=len(registry), rows=rows)


def get_tool_schema(
    tool_name: str,
    *,
    registry_factory: RegistryFactory = _default_registry_factory,
) -> ToolSchemaResult:
    """Return an OpenAI schema for a registered tool."""
    registry = registry_factory()
    tool = registry.get(tool_name)
    if tool is None:
        return ToolSchemaResult(found=False, schema=None)
    return ToolSchemaResult(found=True, schema=tool.to_openai_schema())
