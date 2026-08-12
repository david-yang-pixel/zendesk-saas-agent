from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zd_agent.casework.domain.enums import InvestigationScenario
from zd_agent.casework.policies.investigation_completion import (
    classify_investigation_scenario,
    required_completion_roles,
)


_SKILL_DIR = Path(__file__).parents[2] / "skills" / "dify-ticket-investigation"
_REFERENCE_BY_SCENARIO = {
    InvestigationScenario.ASYNC_PROCESSING: "async-processing.md",
    InvestigationScenario.BILLING_ENTITLEMENT: "billing-entitlement.md",
    InvestigationScenario.RETRIEVAL_RUNTIME: "runtime-failure.md",
    InvestigationScenario.PROVIDER_RUNTIME: "runtime-failure.md",
    InvestigationScenario.WORKFLOW_RUNTIME: "runtime-failure.md",
    InvestigationScenario.GENERAL: "general.md",
}


@dataclass(frozen=True)
class InvestigationSkill:
    name: str
    scenario: InvestigationScenario
    instructions: str
    required_roles: tuple[str, ...]


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text.startswith("---"):
        return text
    _, _, remainder = text.partition("\n---\n")
    return remainder.strip()


def load_investigation_skill(question: str) -> InvestigationSkill:
    scenario = classify_investigation_scenario(question)
    core = _body(_SKILL_DIR / "SKILL.md")
    reference = _body(
        _SKILL_DIR / "references" / _REFERENCE_BY_SCENARIO[scenario],
    )
    roles = tuple(
        role.value for role in sorted(required_completion_roles(question), key=str)
    )
    return InvestigationSkill(
        name="dify-ticket-investigation",
        scenario=scenario,
        instructions=f"{core}\n\n## Active scenario\n\n{reference}",
        required_roles=roles,
    )
