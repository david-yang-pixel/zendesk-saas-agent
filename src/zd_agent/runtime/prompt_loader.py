"""Prompt 加载器：读取 SOUL.md / AGENTS.md / TOOLS.md 并组装 system prompt"""

from __future__ import annotations

from importlib import resources


def load_prompt(filename: str) -> str:
    """从 prompts 包中加载 prompt 文件"""
    ref = resources.files("zd_agent.prompts").joinpath(filename)
    return ref.read_text(encoding="utf-8")


def build_system_prompt() -> str:
    """组装完整的 system prompt"""
    soul = load_prompt("SOUL.md")
    agents = load_prompt("AGENTS.md")
    tools = load_prompt("TOOLS.md")

    return f"""{soul}

---

{agents}

---

{tools}"""
