from zd_agent.session.agent_session import AgentSession


def test_stale_no_attachment_claim_is_corrected_when_images_exist():
    from zd_agent.runtime.image_evidence import guard_against_stale_no_image_claim

    session = AgentSession(ticket_id=3152)
    session.images_injected = True
    text = "注意事项：客户说有示例图片，但 Zendesk 返回无附件或图片。"

    guarded = guard_against_stale_no_image_claim(text, session)

    assert "Zendesk 返回无附件或图片" not in guarded
    assert "当前会话已有客户图片证据" in guarded


def test_stale_no_attachment_claim_is_left_alone_without_image_evidence():
    from zd_agent.runtime.image_evidence import guard_against_stale_no_image_claim

    session = AgentSession(ticket_id=3152)
    text = "注意事项：Zendesk 返回无附件或图片。"

    assert guard_against_stale_no_image_claim(text, session) == text
