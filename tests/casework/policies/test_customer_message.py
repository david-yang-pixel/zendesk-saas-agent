from __future__ import annotations

import pytest

from zd_agent.casework.policies.customer_message import (
    is_definitely_non_substantive_message,
)


@pytest.mark.parametrize(
    "body",
    (
        "Good morning",
        "Good afternoon!",
        "Greetings",
        "Hello everyone",
        "Thanks a lot!",
        "Much appreciated.",
        "Hello there!",
        "Sounds good.",
    ),
)
def test_common_standalone_greetings_and_acks_are_non_substantive(body: str) -> None:
    assert is_definitely_non_substantive_message(body) is True


@pytest.mark.parametrize(
    "body",
    (
        "Good morning, API requests return 429.",
        "Thanks, but the workflow still fails.",
        "Hello, workspace 11111111-1111-4111-8111-111111111111 is affected.",
        "Sounds good, but I still need a refund.",
    ),
)
def test_greeting_prefix_does_not_hide_issue_content(body: str) -> None:
    assert is_definitely_non_substantive_message(body) is False


@pytest.mark.parametrize("body", ("不用了", "算了"))
def test_customer_cancellation_is_not_treated_as_harmless_ack(body: str) -> None:
    assert is_definitely_non_substantive_message(body) is False
