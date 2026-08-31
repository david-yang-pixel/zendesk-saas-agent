from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from zd_agent.casework.domain.models import EvidenceItem
from zd_agent.session.agent_session import AgentSession
from zd_agent.subagents.models import SubagentTask
from zd_agent.subagents.orchestrator import SubagentOrchestrator
from zd_agent.tools.base import Tool, ToolResult


def _task_payload(task: SubagentTask) -> dict[str, Any]:
    return task.model_dump(mode="json")


def _delegated_evidence(tasks: tuple[SubagentTask, ...]) -> list[EvidenceItem]:
    delegated: list[EvidenceItem] = []
    for task in tasks:
        raw_items = task.result.get("evidence", [])
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            try:
                item = EvidenceItem.model_validate(raw_item)
            except ValidationError:
                continue
            if item.source.startswith(("case_context.", "subagent_", "memory_")):
                continue
            evidence_id = f"subagent:{task.id}:{item.evidence_id}"
            delegated.append(
                item.model_copy(
                    update={"evidence_id": evidence_id, "call_id": evidence_id}
                )
            )
    return delegated


class SubagentSpawnTool(Tool):
    name = "subagent_spawn"
    description = (
        "Start one independent ZendeskCasework child session with an explicitly "
        "configured model slot."
    )
    group = "orchestration"
    root_only = True
    is_write_operation = True
    concurrency_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "slot_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
            "idempotency_key": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
        },
        "required": ["slot_id", "objective", "idempotency_key"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        _ = session
        try:
            task = self._orchestrator.spawn(
                slot_id=str(args.get("slot_id") or ""),
                objective=str(args.get("objective") or ""),
                idempotency_key=str(args.get("idempotency_key") or ""),
            )
        except (RuntimeError, ValueError) as error:
            return ToolResult(success=False, error=str(error))
        return ToolResult(
            output=json.dumps(
                {"task_id": task.id, "status": task.status.value},
                sort_keys=True,
            ),
            metadata={"subagent_task_id": task.id},
        )


class SubagentWaitTool(Tool):
    name = "subagent_wait"
    description = "Wait briefly for one or more child sessions and collect results."
    group = "orchestration"
    root_only = True
    parameters = {
        "type": "object",
        "properties": {
            "task_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 12,
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 0,
                "maximum": 60,
            },
        },
        "required": ["task_ids"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        _ = session
        task_ids = tuple(str(task_id) for task_id in args.get("task_ids") or ())
        try:
            tasks = await self._orchestrator.wait(
                task_ids,
                timeout_seconds=float(args.get("timeout_seconds") or 0),
            )
        except KeyError:
            return ToolResult(success=False, error="subagent task not found")
        results = [task.result for task in tasks if task.result]
        delegated = _delegated_evidence(tasks)
        ledger_ids_by_task: dict[str, list[str]] = {task.id: [] for task in tasks}
        for item in delegated:
            _, task_id, _ = item.evidence_id.split(":", maxsplit=2)
            ledger_ids_by_task.setdefault(task_id, []).append(item.evidence_id)
        public_tasks = []
        for task in tasks:
            payload = _task_payload(task)
            payload["ledger_evidence_ids"] = ledger_ids_by_task.get(task.id, [])
            public_tasks.append(payload)
        return ToolResult(
            output=json.dumps(
                {"tasks": public_tasks},
                ensure_ascii=False,
                sort_keys=True,
            ),
            metadata={
                "subagent_task_ids": [task.id for task in tasks],
                "subagent_results": results,
                "casework_evidence_items": [
                    item.model_dump(mode="json") for item in delegated
                ],
            },
        )


class SubagentStatusTool(Tool):
    name = "subagent_status"
    description = "Read the durable status of one child session."
    group = "orchestration"
    root_only = True
    parameters = {
        "type": "object",
        "properties": {"task_id": {"type": "string", "minLength": 1}},
        "required": ["task_id"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        _ = session
        try:
            task = self._orchestrator.status(str(args.get("task_id") or ""))
        except KeyError:
            return ToolResult(success=False, error="subagent task not found")
        return ToolResult(output=json.dumps(_task_payload(task), sort_keys=True))


class SubagentCancelTool(Tool):
    name = "subagent_cancel"
    description = "Cancel one child session owned by the current root session."
    group = "orchestration"
    root_only = True
    is_write_operation = True
    concurrency_safe = False
    parameters = {
        "type": "object",
        "properties": {"task_id": {"type": "string", "minLength": 1}},
        "required": ["task_id"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        _ = session
        try:
            task = await self._orchestrator.cancel(str(args.get("task_id") or ""))
        except KeyError:
            return ToolResult(success=False, error="subagent task not found")
        return ToolResult(output=json.dumps(_task_payload(task), sort_keys=True))


def create_subagent_control_tools(
    orchestrator: SubagentOrchestrator,
) -> tuple[Tool, ...]:
    return (
        SubagentSpawnTool(orchestrator),
        SubagentWaitTool(orchestrator),
        SubagentStatusTool(orchestrator),
        SubagentCancelTool(orchestrator),
    )
