from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from typing import Literal, TypeAlias

TicketType: TypeAlias = Literal[
    "platform_issue",
    "knowledge_base",
    "workflow",
    "api",
    "permissions",
    "product_usage",
    "billing",
    "refund",
    "insufficient_info",
    "mention",
    "general",
]

DEFAULT_TICKET_TYPE: TicketType = "general"
TICKET_TYPES: tuple[TicketType, ...] = (
    "platform_issue",
    "knowledge_base",
    "workflow",
    "api",
    "permissions",
    "product_usage",
    "billing",
    "refund",
    "insufficient_info",
    "mention",
    "general",
)
_TICKET_TYPE_BY_NAME: dict[str, TicketType] = {
    ticket_type: ticket_type for ticket_type in TICKET_TYPES
}
TICKET_TYPE_SET = frozenset(TICKET_TYPES)


@dataclass(frozen=True)
class PromptSkill:
    name: str
    file_name: str = ""
    description: str = ""
    content: str = ""


AVAILABLE_SKILLS: tuple[PromptSkill, ...] = (
    PromptSkill("dify_support_ticket", "DIFY_SUPPORT_TICKET.md"),
)
DEFAULT_ENABLED_SKILLS = ("dify_support_ticket",)
AVAILABLE_SKILL_NAMES = tuple(skill.name for skill in AVAILABLE_SKILLS)
_SKILLS_BY_NAME = {skill.name: skill for skill in AVAILABLE_SKILLS}


def normalize_ticket_type(value: str) -> TicketType:
    normalized = value.strip().lower()
    return _TICKET_TYPE_BY_NAME.get(normalized, DEFAULT_TICKET_TYPE)


def normalize_skill_names(
    values: list[str],
    *,
    custom_names: Iterable[str] = (),
) -> list[str]:
    allowed = {*AVAILABLE_SKILL_NAMES, *custom_names}
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = value.strip()
        if name not in allowed or name in seen:
            continue
        normalized.append(name)
        seen.add(name)
    return normalized


def enabled_prompt_skills() -> list[PromptSkill]:
    from zd_agent.config import get_config

    cfg = get_config()
    custom_skills = [
        PromptSkill(
            name=skill.name,
            description=skill.description,
            content=skill.content,
        )
        for skill in cfg.skills.custom
        if skill.enabled and skill.name.strip() and skill.content.strip()
    ]
    custom_by_name = {skill.name: skill for skill in custom_skills}
    configured = normalize_skill_names(
        list(cfg.skills.enabled),
        custom_names=custom_by_name,
    )
    if not configured:
        configured = list(DEFAULT_ENABLED_SKILLS)
    skills_by_name = {**_SKILLS_BY_NAME, **custom_by_name}
    active = [skills_by_name[name] for name in configured if name in skills_by_name]
    if active:
        return active
    return [_SKILLS_BY_NAME[name] for name in DEFAULT_ENABLED_SKILLS]


def load_skill_context(skill: PromptSkill) -> str:
    if skill.content:
        parts = [f"# Skill: {skill.name}"]
        if skill.description:
            parts.extend(["", skill.description])
        parts.extend(["", skill.content.strip()])
        return "\n".join(parts)
    ref = resources.files("zd_agent.prompts.skills").joinpath(skill.file_name)
    return ref.read_text(encoding="utf-8")


def load_ticket_strategy(ticket_type: TicketType) -> str:
    ref = resources.files("zd_agent.prompts.strategies").joinpath(
        f"{ticket_type}.md",
    )
    return ref.read_text(encoding="utf-8")


def build_ticket_skill_context(
    *,
    ticket_type: TicketType,
    strategy: str,
    lazy_sections: str = "",
) -> str:
    active_skills = enabled_prompt_skills()
    active_names = ", ".join(skill.name for skill in active_skills)
    parts = [
        "## 已激活 Skills",
        f"本次工单启用的 Skills: {active_names}",
        *(load_skill_context(skill) for skill in active_skills),
        f"## 类型策略 Skill: {ticket_type}\n\n{strategy}",
    ]
    if lazy_sections:
        parts.extend(["## 按需加载证据上下文", lazy_sections])
    return "\n\n---\n\n".join(parts)
