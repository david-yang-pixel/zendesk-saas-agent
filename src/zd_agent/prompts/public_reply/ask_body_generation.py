from __future__ import annotations

import json
from typing import Final

import httpx

from zd_agent.log import get_logger

logger = get_logger(module="prompts.public_reply.ask_body")

MISSING_INFO_FALLBACK_BULLETS: Final[tuple[str, ...]] = (
    "- What type of issue this is, such as billing/refund, feature usage, account/subscription, workflow, knowledge base, API, or an error/bug",
    "- A short description of what you are trying to do or what happened",
    "- Any relevant page, app, workflow, dataset, API, billing/account area, error text, or screenshot if there is one",
)

ASK_BODY_MAX_CHARS: Final = 900

ASK_BODY_PROMPT: Final = """You write one customer-visible Zendesk public reply for Dify SaaS support.

Scenario:
{scenario}

Customer name already known:
{customer_name}

Recent customer messages:
<customer_messages>
{messages}
</customer_messages>

Write the reply body only. Another layer will append the AI disclosure footer.

Rules:
- Use English only.
- Keep it concise: one short paragraph plus up to 3 bullets if bullets help.
- Ask only for the minimum information needed to make the ticket actionable.
- Do not ask for customer name, email, Workspace ID, plan, or account owner; those are already available from the form or ticket metadata.
- Do not mention internal state, trigger decider, confidence, LLM, templates, automation, or tools.
- Do not claim investigation has already happened.
- For a ticket with no actual request yet, ask what type of issue the customer is encountering, so support can choose the right path. Mention concise examples across support categories: billing/refund, feature usage/how-to, account/subscription, workflow, knowledge base, API, or an error/bug.
- Do not assume every vague ticket is a troubleshooting or bug report. Ask for error text, screenshots, affected feature, and reproduction steps only when the customer indicates an error, failure, bug, or unexpected behavior.
- For a vague request, infer the likely issue type from the customer's words and ask only for the smallest details needed to proceed. Refund/billing may need amount/date/subscription action; feature usage may need desired outcome and current setup; account/subscription may need affected account or action; technical errors may need affected page/feature, exact error, screenshot, and steps.
- For a repeat ask, be direct and ask for the smallest set of exact details still missing. Do not apologize repeatedly.

Return JSON exactly: {{"body": "..."}}"""

FORBIDDEN_BODY_PHRASES: Final[tuple[str, ...]] = (
    "workspace id",
    "workspace_id",
    "organization id",
    "org id",
    "email address",
    "account email",
    "your email",
    "your plan",
    "your subscription",
    "owner email",
    "your account name",
    "customer name",
    "dify saas agent",
    "reply with",
    "support engineer will take",
    "internal note",
    "trigger decider",
    "trigger_decider",
    "template",
    "llm",
)


def _admin_ask_prompt_override() -> str:
    from zd_agent.config import get_config

    return get_config().prompts.ask_extra_instructions.strip()


def _scenario_instruction(*, verdict_kind: str, ask_count: int) -> str:
    if ask_count >= 1:
        return (
            "The customer has already received at least one ask, but the ticket "
            "still does not contain enough actionable detail."
        )
    match verdict_kind:
        case "no_message":
            return (
                "The ticket was created from form data, but the customer has not "
                "described their actual support request yet."
            )
        case "greeting":
            return (
                "The customer only greeted the support channel and has not stated "
                "a concrete Dify request yet."
            )
        case _:
            return (
                "The customer mentioned a request, but important details "
                "are missing."
            )


def _customer_message_context(customer_messages: list[str]) -> str:
    recent = [message.strip() for message in customer_messages[-5:] if message.strip()]
    if not recent:
        return "(no customer request message yet)"
    return "\n---\n".join(recent)


def _is_unsafe_generated_body(body: str) -> bool:
    low = body.lower()
    return (
        len(body) < 20
        or len(body) > ASK_BODY_MAX_CHARS
        or any(phrase in low for phrase in FORBIDDEN_BODY_PHRASES)
    )


def _extract_generated_ask_body(resp: httpx.Response) -> str | None:
    data = resp.json()
    raw = data["choices"][0]["message"]["content"]
    parsed = json.loads(raw)
    body = parsed.get("body")
    if not isinstance(body, str):
        return None
    body = body.strip()
    if _is_unsafe_generated_body(body):
        return None
    return body


async def generate_ask_body_with_llm(
    *,
    verdict_kind: str,
    ask_count: int,
    customer_name: str,
    customer_messages: list[str],
    model: str,
    api_key: str,
    timeout: float = 10.0,
) -> str | None:
    scenario = _scenario_instruction(
        verdict_kind=verdict_kind,
        ask_count=ask_count,
    )
    prompt = ASK_BODY_PROMPT.format(
        scenario=scenario,
        customer_name=customer_name or "(not available)",
        messages=_customer_message_context(customer_messages),
    )
    override = _admin_ask_prompt_override()
    if override:
        prompt = f"{prompt}\n\nAdmin-managed additional rules:\n{override}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 240,
                    "response_format": {"type": "json_object"},
                },
            )
    except httpx.HTTPError as e:
        logger.warning("ask_body_llm_http_error", error=str(e)[:200])
        return None

    if resp.status_code != 200:
        logger.warning(
            "ask_body_llm_bad_status",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return None

    try:
        return _extract_generated_ask_body(resp)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning("ask_body_llm_parse_error", error=str(e)[:200])
        return None
