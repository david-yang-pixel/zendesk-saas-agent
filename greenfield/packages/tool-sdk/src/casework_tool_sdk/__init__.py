"""Typed investigation-tool registry and bounded Tool Loop."""

from casework_tool_sdk.artifacts import ArtifactRecord, ArtifactStore, FileArtifactStore
from casework_tool_sdk.loop import ToolLoop, ToolLoopResult
from casework_tool_sdk.registry import (
    ToolExecutionContext,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
)

__all__ = [
    "ArtifactRecord",
    "ArtifactStore",
    "FileArtifactStore",
    "ToolExecutionContext",
    "ToolExecutionError",
    "ToolLoop",
    "ToolLoopResult",
    "ToolNotFoundError",
    "ToolRegistry",
]
