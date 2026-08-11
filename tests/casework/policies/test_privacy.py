import pytest

from zd_agent.casework.policies.privacy import claim_is_customer_safe


@pytest.mark.parametrize(
    "statement",
    (
        "The affected tenant ID is 123456789.",
        "The failing span is 5f2d3a4b6c7d8e9f.",
        "The backend host is mysql.prod.svc.cluster.local.",
        "The node is ip-10-2-3-4.us-west-2.compute.internal.",
        "The configured credential is sk-proj-AbCdEf1234567890.",
        "The internal address is 10.2.3.4.",
        "<at id=all></at> The issue requires review.",
        "<font color=red>URGENT</font>",
    ),
)
def test_customer_claim_rejects_internal_values_and_active_markup(
    statement: str,
) -> None:
    assert claim_is_customer_safe(statement) is False


@pytest.mark.parametrize(
    "statement",
    (
        "Upload your logs so we can continue.",
        "Kindly send the screenshot again.",
        "Let us know which workspace is affected.",
        "You must share your API key to proceed.",
        "It would help if you provided your email address.",
        "Your next step is to send us the full request trace.",
    ),
)
def test_customer_claim_cannot_smuggle_an_untyped_request(statement: str) -> None:
    assert claim_is_customer_safe(statement) is False


@pytest.mark.parametrize(
    "statement",
    (
        "The Professional subscription is active.",
        "The latest request completed successfully.",
        "The screenshot shows a quota warning.",
    ),
)
def test_customer_claim_allows_plain_evidence_grounded_facts(statement: str) -> None:
    assert claim_is_customer_safe(statement) is True
