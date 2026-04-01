"""分段注册 + 条件组装的 prompt 构建器。"""

from __future__ import annotations

from zd_agent.runtime.prompt.section import PromptContext, PromptSection


class PromptBuilder:
    """维护已注册段并按上下文构建最终文本。"""

    def __init__(self) -> None:
        self._sections: list[PromptSection] = []

    def register(self, section: PromptSection) -> None:
        """注册一个 prompt 段。"""
        self._sections.append(section)

    def build(self, ctx: PromptContext) -> str:
        """按条件过滤、排序、拼接所有 section。"""
        active: list[tuple[int, str, str]] = []
        for s in self._sections:
            if s.condition is not None and not s.condition(ctx):
                continue
            content = s.content(ctx) if callable(s.content) else s.content
            if not content.strip():
                continue
            if s.wrap_tag:
                content = f"<{s.wrap_tag}>\n{content}\n</{s.wrap_tag}>"
            active.append((s.order, s.name, content))
        active.sort(key=lambda x: x[0])
        return "\n\n---\n\n".join(item[2] for item in active)
