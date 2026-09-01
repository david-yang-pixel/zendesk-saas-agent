"""Evidence admission and final-answer completion gate."""

from __future__ import annotations

from contracts.evidence import Evidence, EvidenceStatus
from contracts.tool import ToolResult, ToolResultStatus


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

    def records(self) -> tuple[Evidence, ...]:
        return tuple(self._evidence.values())
