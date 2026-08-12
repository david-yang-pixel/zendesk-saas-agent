from zd_agent.casework.adapters.investigation_skill import load_investigation_skill
from zd_agent.casework.domain.enums import InvestigationScenario


def test_spanish_stuck_document_case_loads_async_processing_skill() -> None:
    skill = load_investigation_skill(
        "Tengo documentos atascados. Ahora están en pausa y no sé qué falla.",
    )

    assert skill.scenario is InvestigationScenario.ASYNC_PROCESSING
    assert skill.required_roles == ("current_state", "failing_boundary")
    assert "API persistence -> tenant queue append" in skill.instructions
    assert "Absence of `ERROR` or `FATAL` logs is not proof" in skill.instructions


def test_product_question_loads_general_skill_without_runtime_gate() -> None:
    skill = load_investigation_skill("Is this API behavior supported?")

    assert skill.scenario is InvestigationScenario.GENERAL
    assert skill.required_roles == ()
    assert "customer's actual question" in skill.instructions
