"""Evidence admission and final-answer completion gate."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from contracts.evidence import Evidence, EvidenceStatus
from contracts.tool import ToolResult, ToolResultStatus

if TYPE_CHECKING:
    from casework_agent_core.subagents import SubagentReport


class EvidenceGate:
    def __init__(self) -> None:
        self._evidence: dict[str, Evidence] = {}

    def admit(self, *, call_id: str, tool_name: str, result: ToolResult) -> Evidence:
        if result.status is ToolResultStatus.FAILURE or not result.evidence_id:
            raise ValueError("tool result cannot be admitted as evidence")
        evidence = Evidence(
            evidence_id=result.evidence_id,
            call_id=call_id,
            source=tool_name,
            status=(
                EvidenceStatus.SUCCESS
                if result.status is ToolResultStatus.SUCCESS
                else EvidenceStatus.PARTIAL
            ),
            observed_at=result.observed_at,
            targets=result.targets,
            payload=result.data if result.data is not None else result.summary,
            redacted=True,
        )
        self._evidence[evidence.evidence_id] = evidence
        return evidence

    def can_answer(self, *, answer: str, evidence_ids: tuple[str, ...]) -> bool:
        return bool(answer.strip()) and bool(evidence_ids) and all(
            evidence_id in self._evidence for evidence_id in evidence_ids
        )

    def admit_subagent_report(
        self, *, task_id: str, report: SubagentReport
    ) -> tuple[str, ...]:
        """Bind child evidence to the root gate with explicit provenance."""

        if not report.evidence_ids:
            raise ValueError("subagent report cannot be admitted without evidence")
        observed_at = datetime.now(UTC)
        for evidence_id in report.evidence_ids:
            self._evidence[evidence_id] = Evidence(
                evidence_id=evidence_id,
                call_id=f"subagent-{task_id}",
                source=f"subagent:{task_id}",
                observed_at=observed_at,
                payload=report.summary,
                redacted=True,
            )
        return report.evidence_ids

    def records(self) -> tuple[Evidence, ...]:
        return tuple(self._evidence.values())
