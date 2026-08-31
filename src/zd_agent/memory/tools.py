from __future__ import annotations

import json
from typing import Any

from zd_agent.memory.models import MemoryKind
from zd_agent.memory.store import MemoryStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult


def _json(data: Any) -> str:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


class MemorySearchTool(Tool):
    name = "memory_search"
    description = (
        "Search reviewed historical guidance. Results are navigation only and "
        "cannot support current-ticket claims without current evidence."
    )
    group = "memory"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    }

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        _ = session
        hits = self._store.search(
            str(args.get("query") or ""), limit=int(args.get("limit") or 10)
        )
        return ToolResult(
            output=_json(
                {
                    "authority": "historical_guidance",
                    "may_support_current_claims": False,
                    "disclaimer": (
                        "Re-verify every material fact with current authoritative tools."
                    ),
                    "results": [
                        {
                            **hit.entry.model_dump(mode="json"),
                            "authority": hit.authority,
                            "disclaimer": hit.disclaimer,
                        }
                        for hit in hits
                    ],
                }
            )
        )


class MemoryGetTool(Tool):
    name = "memory_get"
    description = "Read one reviewed historical memory by ID."
    group = "memory"
    parameters = {
        "type": "object",
        "properties": {"id": {"type": "string", "minLength": 1}},
        "required": ["id"],
    }

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        _ = session
        entry = self._store.get(str(args.get("id") or ""))
        if entry is None:
            return ToolResult(success=False, error="active memory entry not found")
        return ToolResult(
            output=_json(
                {
                    "authority": "historical_guidance",
                    "may_support_current_claims": False,
                    "entry": entry,
                }
            )
        )


class MemoryProposeTool(Tool):
    name = "memory_propose"
    description = "Propose review-gated long-term memory from the current ticket."
    group = "memory"
    is_write_operation = True
    concurrency_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["episodic", "semantic"]},
            "title": {"type": "string", "minLength": 1},
            "content": {"type": "string", "minLength": 1},
            "tags": {"type": "array", "items": {"type": "string"}},
            "source_ticket_id": {"type": "integer", "minimum": 1},
            "evidence_references": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
            },
        },
        "required": ["kind", "title", "content", "evidence_references"],
    }

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        requested_ticket = int(args.get("source_ticket_id") or session.ticket_id)
        if requested_ticket != session.ticket_id:
            return ToolResult(
                success=False,
                error="memory proposals may reference only the current ticket",
            )
        entry = self._store.propose(
            kind=MemoryKind(str(args.get("kind") or "")),
            title=str(args.get("title") or ""),
            content=str(args.get("content") or ""),
            tags=tuple(str(tag) for tag in args.get("tags") or ()),
            source_ticket_id=session.ticket_id,
            evidence_references=tuple(
                str(ref) for ref in args.get("evidence_references") or ()
            ),
            proposed_by=session.session_id,
        )
        return ToolResult(output=_json(entry))


class MemoryFeedbackTool(Tool):
    name = "memory_feedback"
    description = "Record whether reviewed historical memory helped this session."
    group = "memory"
    is_write_operation = True
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "useful": {"type": "boolean"},
            "note": {"type": "string"},
        },
        "required": ["id", "useful"],
    }

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        entry_id = str(args.get("id") or "")
        if self._store.get(entry_id) is None:
            return ToolResult(success=False, error="active memory entry not found")
        feedback = self._store.add_feedback(
            entry_id,
            useful=bool(args.get("useful")),
            note=str(args.get("note") or ""),
            actor=session.session_id,
        )
        return ToolResult(output=_json(feedback))


class WorkingMemoryGetTool(Tool):
    name = "memory_working_get"
    description = "Read shared working memory for this root casework session."
    group = "memory"
    parameters = {
        "type": "object",
        "properties": {"key": {"type": "string", "minLength": 1}},
        "required": ["key"],
    }

    def __init__(self, store: MemoryStore, root_session_id: str) -> None:
        self._store = store
        self._root_session_id = root_session_id

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        _ = session
        item = self._store.get_working(
            self._root_session_id, str(args.get("key") or "")
        )
        if item is None:
            return ToolResult(success=False, error="working memory key not found")
        return ToolResult(output=_json(item))


class WorkingMemoryPutTool(Tool):
    name = "memory_working_put"
    description = "Write shared working memory for this root casework session."
    group = "memory"
    is_write_operation = True
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "minLength": 1},
            "value": {},
        },
        "required": ["key", "value"],
    }

    def __init__(self, store: MemoryStore, root_session_id: str) -> None:
        self._store = store
        self._root_session_id = root_session_id

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        item = self._store.put_working(
            root_session_id=self._root_session_id,
            key=str(args.get("key") or ""),
            value=args.get("value"),
            writer_session_id=session.session_id,
        )
        return ToolResult(output=_json(item))


def create_memory_tools(
    store: MemoryStore, root_session_id: str
) -> tuple[Tool, ...]:
    return (
        MemorySearchTool(store),
        MemoryGetTool(store),
        MemoryProposeTool(store),
        MemoryFeedbackTool(store),
        WorkingMemoryGetTool(store, root_session_id),
        WorkingMemoryPutTool(store, root_session_id),
    )
