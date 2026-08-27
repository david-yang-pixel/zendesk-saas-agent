from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zd_agent.casework.domain.enums import InvestigationScenario
from zd_agent.casework.policies.investigation_completion import (
    classify_investigation_scenario,
    required_completion_roles,
)
from zd_agent.services.instruction_store import InstructionSnapshot, InstructionStore


@dataclass(frozen=True)
class InvestigationSkill:
    name: str
    scenario: InvestigationScenario
    instructions: str
    required_roles: tuple[str, ...]


def _build_investigation_skill(
    question: str,
    *,
    snapshot: InstructionSnapshot,
) -> InvestigationSkill:
    scenario = classify_investigation_scenario(question)
    reference_key = {
        InvestigationScenario.ASYNC_PROCESSING: "async_processing",
        InvestigationScenario.BILLING_ENTITLEMENT: "billing_entitlement",
        InvestigationScenario.RETRIEVAL_RUNTIME: "runtime_failure",
        InvestigationScenario.PROVIDER_RUNTIME: "runtime_failure",
        InvestigationScenario.WORKFLOW_RUNTIME: "runtime_failure",
        InvestigationScenario.GENERAL: "general",
    }[scenario]
    reference = snapshot.reference_for(reference_key)
    roles = tuple(
        role.value for role in sorted(required_completion_roles(question), key=str)
    )
    return InvestigationSkill(
        name="dify-ticket-investigation",
        scenario=scenario,
        instructions=(f"{snapshot.skill_core}\n\n## Active scenario\n\n{reference}"),
        required_roles=roles,
    )


def load_investigation_skill(
    question: str,
    *,
    instruction_store: InstructionStore | None = None,
    snapshot: InstructionSnapshot | None = None,
) -> InvestigationSkill:
    """Load the skill using a frozen store snapshot when one is supplied.

    The no-argument path remains the reliable code-default fallback used by
    replay/unit callers.  Runtime callers pass an ``InstructionStore`` and
    freeze its values at investigator construction time.
    """
    if snapshot is None:
        snapshot = (
            instruction_store.snapshot()
            if instruction_store is not None
            else InstructionStore(Path("/nonexistent")).defaults()
        )
    return _build_investigation_skill(question, snapshot=snapshot)
