from __future__ import annotations

from typing import Any, cast

from casework_ingress.processing_rules import (
    NormalizedZendeskEvent,
    ProcessingRuleConfig,
    evaluate_processing_rules,
)


def _event(**overrides: object) -> NormalizedZendeskEvent:
    values: dict[str, object] = {
        "ticket_id": 3726,
        "tags": ("priority", "SaaS"),
        "assignee": "saas-support",
        "status": "OPEN",
        "is_customer_message": True,
        "message_text": "The customer question",
    }
    values.update(overrides)
    return NormalizedZendeskEvent(**cast(dict[str, Any], values))


def test_matching_customer_event_enqueues_only_ticket_id() -> None:
    decision = evaluate_processing_rules(
        _event(),
        config=ProcessingRuleConfig(
            saas_tag="saas",
            saas_assignee="saas-support",
        ),
    )

    assert decision.action == "enqueue"
    assert decision.reason == "eligible"
    assert decision.ticket_id == 3726
    payload = decision.business_payload
    assert payload == {"ticket_id": 3726}
    assert payload is not None
    assert set(payload) == {"ticket_id"}


def test_event_is_ignored_when_any_normal_processing_condition_is_missing() -> None:
    config = ProcessingRuleConfig(saas_tag="saas", saas_assignee="saas-support")

    assert evaluate_processing_rules(
        _event(tags=("priority",)), config=config
    ).reason == "saas_tag_missing"
    assert evaluate_processing_rules(
        _event(assignee="another-team"), config=config
    ).reason == "saas_assignee_mismatch"
    assert (
        evaluate_processing_rules(_event(status="pending"), config=config).reason
        == "status_out_of_scope"
    )
    assert evaluate_processing_rules(
        _event(is_customer_message=False), config=config
    ).reason == "not_customer_message"
    assert evaluate_processing_rules(
        _event(assignee_id=45433304089364), config=config
    ).reason == "already_assigned_to_human"


def test_human_keyword_cancels_independently_of_normal_conditions() -> None:
    decision = evaluate_processing_rules(
        _event(
            tags=(),
            assignee="another-team",
            status="solved",
            is_customer_message=True,
            message_text="  HuMaN  ",
        ),
        config=ProcessingRuleConfig(
            saas_tag="saas",
            saas_assignee="saas-support",
        ),
    )

    assert decision.action == "cancel"
    assert decision.reason == "human"
    assert decision.ticket_id == 3726
    assert decision.business_payload is None


def test_human_aliases_match_case_insensitively_but_only_as_exact_text() -> None:
    config = ProcessingRuleConfig(
        saas_tag="saas",
        saas_assignee="saas-support",
        human_aliases=("human agent", "真人"),
    )

    assert evaluate_processing_rules(
        _event(message_text=" HUMAN AGENT "), config=config
    ).reason == "human"
    assert (
        evaluate_processing_rules(_event(message_text="真人"), config=config).reason
        == "human"
    )
    assert evaluate_processing_rules(
        _event(message_text="Please connect me to a human agent"), config=config
    ).action == "enqueue"
    assert evaluate_processing_rules(
        _event(message_text="human agent, please"), config=config
    ).action == "enqueue"


def test_human_normalization_accepts_width_and_surrounding_punctuation() -> None:
    config = ProcessingRuleConfig(
        saas_tag="saas",
        saas_assignee="saas-support",
        human_aliases=("真人",),
    )

    assert evaluate_processing_rules(
        _event(message_text="（ＨＵＭＡＮ！）"),
        config=config,
    ).action == "cancel"
    assert evaluate_processing_rules(
        _event(message_text="「真人」"), config=config
    ).action == "cancel"
    assert evaluate_processing_rules(
        _event(message_text="please human!"), config=config
    ).action == "enqueue"


def test_human_text_in_non_customer_message_does_not_cancel() -> None:
    decision = evaluate_processing_rules(
        _event(is_customer_message=False, message_text="human"),
        config=ProcessingRuleConfig(
            saas_tag="saas", saas_assignee="saas-support"
        ),
    )

    assert decision.action == "ignore"
    assert decision.reason == "not_customer_message"


def test_agent_generated_draft_with_quoted_mention_does_not_retrigger() -> None:
    decision = evaluate_processing_rules(
        _event(
            is_customer_message=False,
            message_text="[DIFY_AI_AGENT_DRAFT:job-123] Please ask @David Yang to review.",
        ),
        config=ProcessingRuleConfig(saas_tag="saas", saas_assignee="saas-support"),
    )
    assert decision.action == "ignore"
    assert decision.reason == "not_customer_message"
