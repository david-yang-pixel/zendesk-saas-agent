from datetime import UTC, datetime
from pathlib import Path

import pytest

from casework_tool_sdk.artifacts import FileArtifactStore
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.tool import ToolDefinition, ToolResult, ToolResultStatus


@pytest.mark.asyncio
async def test_large_tool_data_is_replaced_with_scoped_artifact_reference(
    tmp_path: Path,
) -> None:
    store = FileArtifactStore(tmp_path, session_id="session-3726")
    registry = ToolRegistry(artifact_store=store, max_inline_bytes=32)
    registry.register(
        ToolDefinition(
            name="log_search",
            description="Search logs",
            input_schema={"type": "object"},
        ),
        lambda arguments, context: ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary="Found matching logs",
            data={"lines": ["x" * 128]},
            evidence_id="ev-logs",
            observed_at=datetime.now(UTC),
        ),
    )

    result = await registry.execute(
        "log_search",
        {},
        context=ToolExecutionContext(
            ticket_id=3726,
            session_id="session-3726",
            root_session_id="session-3726",
        ),
    )

    assert result.truncated is True
    assert len(result.artifacts) == 1
    artifact_ref = result.artifacts[0]
    assert artifact_ref.startswith("artifact://session-3726/")
    assert result.data == {
        "artifact_ref": artifact_ref,
        "bytes": 142,
        "media_type": "application/json",
        "sha256": artifact_ref.rsplit("/", 1)[1].removesuffix(".json"),
    }
    assert store.read_json(artifact_ref) == {"lines": ["x" * 128]}


def test_artifact_store_rejects_cross_session_and_invalid_references(
    tmp_path: Path,
) -> None:
    store = FileArtifactStore(tmp_path, session_id="session-3726")

    with pytest.raises(ValueError, match="session"):
        store.read_json("artifact://other-session/deadbeef.json")
    with pytest.raises(ValueError, match="reference"):
        store.read_json("../../etc/passwd")


@pytest.mark.asyncio
async def test_small_tool_data_remains_inline(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path, session_id="session-3726")
    registry = ToolRegistry(artifact_store=store, max_inline_bytes=1024)
    registry.register(
        ToolDefinition(
            name="lookup",
            description="Lookup workspace",
            input_schema={"type": "object"},
        ),
        lambda arguments, context: ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary="Found workspace",
            data={"workspace_id": "ws-1"},
            evidence_id="ev-workspace",
            observed_at=datetime.now(UTC),
        ),
    )

    result = await registry.execute(
        "lookup",
        {},
        context=ToolExecutionContext(
            ticket_id=3726,
            session_id="session-3726",
            root_session_id="session-3726",
        ),
    )

    assert result.data == {"workspace_id": "ws-1"}
    assert result.artifacts == ()
    assert result.truncated is False
