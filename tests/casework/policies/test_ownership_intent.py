from __future__ import annotations

import pytest

from zd_agent.casework.policies.ownership import (
    customer_human_request_active,
    customer_requested_human,
)


@pytest.mark.parametrize(
    "message",
    [
        "human",
        "Human",
        "human.",
        "“human”",
        "I need a human support engineer",
        "Please connect me to a support agent.",
        "Can you transfer us to a human?",
        "I don't know the cause; please transfer me to a human.",
    ],
)
def test_customer_human_command_is_recognized(message: str) -> None:
    assert customer_requested_human(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "I do not want a human",
        "No human is needed",
        "human-readable output",
        "I need help connecting my app to our human resources system.",
        "Can a human-readable output be generated? I need it for parsing.",
        "I want to connect a human-in-the-loop node.",
    ],
)
def test_negated_or_unrelated_human_text_does_not_request_takeover(
    message: str,
) -> None:
    assert customer_requested_human(message) is False


def test_human_command_remains_active_across_same_customer_burst() -> None:
    assert (
        customer_human_request_active(
            ("human", "Here is another screenshot and the request ID."),
        )
        is True
    )


def test_unrelated_human_adjective_does_not_cancel_sticky_intent() -> None:
    assert (
        customer_human_request_active(
            ("human", "The exported report is not human-readable."),
        )
        is True
    )


def test_explicit_human_request_cancellation_clears_sticky_intent() -> None:
    assert (
        customer_human_request_active(
            ("human", "No human is needed; please continue troubleshooting here."),
        )
        is False
    )
