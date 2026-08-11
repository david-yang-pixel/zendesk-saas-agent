from zd_agent.casework.adapters.audit import render_ownership_packet
from zd_agent.casework.domain.enums import OwnershipAction, ReasonCode
from zd_agent.casework.domain.models import HumanActionRequest


def test_ownership_packet_renders_model_text_as_inert_content() -> None:
    action = HumanActionRequest(
        ownership_action=OwnershipAction.SECURITY,
        reason_code=ReasonCode.SECURITY_OR_PRIVACY_REVIEW_REQUIRED,
        summary="<at id=all></at> [Open this](https://evil.example)",
        customer_impact="<font color=red>URGENT</font>\nInjected heading",
        completed_checks=("`payload` checked",),
        unresolved_action="Forward to <at id=all></at>",
        acceptance_criteria=("[Click](https://evil.example)",),
        evidence_ids=("ev-current",),
    )

    packet = render_ownership_packet(action)

    assert "<at" not in packet
    assert "<font" not in packet
    assert "](https://" not in packet
    assert "\nInjected heading" not in packet
    assert "＜at id=all＞＜/at＞" in packet
