"""知识库工具组：搜索和读取本地 SaaS 知识库的文档。

知识库实际路径优先级：
  1. cfg.knowledge.docs_dir 如果存在
  2. {cfg.resources.base_dir}/{cfg.resources.knowledge_base_dir}（默认指向
     /opt/zd-agent/resources/saas版知识库/，SaaS 部署的真实位置）
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from zd_agent.config import get_config
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

_SEARCH_EXTENSIONS = {".md", ".json", ".txt", ".yaml", ".yml"}
_MAX_MATCHES = 30
_MAX_OUTPUT_CHARS = 5000
_CONTEXT_LINES = 2
_SEARCH_COMMAND_TIMEOUT_SECONDS = 10
_TICKET_RECORD_PATH_NAMES = {"工单记录", "历史工单"}


def _resolve_docs_dir() -> Path | None:
    """统一解析 SaaS 知识库目录。

    优先使用 ``cfg.resources.base_dir / cfg.resources.knowledge_base_dir``
    （生产部署的真实 saas 知识库位置）；这个路径不存在时才退到
    ``cfg.knowledge.docs_dir``。

    注意：``knowledge.docs_dir`` 默认 ``./ai-docs`` 与 ``archive_dir`` 同根，
    archive_ticket 写入的工单分析也在那里。如果优先选 docs_dir 会让
    knowledge_search **搜到 agent 自己写的工单 archive 而不是真实知识库**——
    所以这里把 resources 的 knowledge_base_dir 设为优先。
    """
    cfg = get_config()
    primary = Path(cfg.resources.base_dir) / cfg.resources.knowledge_base_dir
    if primary.exists() and primary.is_dir():
        return primary
    fallback = Path(cfg.knowledge.docs_dir)
    if fallback.exists() and fallback.is_dir():
        return fallback
    return None


def _resolve_search_dir(docs_dir: Path, raw_path: Any) -> tuple[Path | None, str | None]:
    """Resolve an optional search subdirectory under ``docs_dir`` safely."""
    docs_dir = docs_dir.resolve()
    if raw_path is None or raw_path == "":
        return docs_dir, None
    if not isinstance(raw_path, str):
        return None, "path 必须是字符串"

    normalized = raw_path.strip().strip("/")
    if not normalized:
        return docs_dir, None

    first_part = Path(normalized).parts[0] if Path(normalized).parts else normalized
    if first_part in _TICKET_RECORD_PATH_NAMES:
        return (
            None,
            "历史工单记录不在 knowledge_search 知识库目录里；请改用 "
            "ticket_record_search / ticket_record_read。",
        )

    search_dir = (docs_dir / normalized).resolve()
    try:
        search_dir.relative_to(docs_dir)
    except ValueError:
        return None, "路径超出知识库范围"

    if not search_dir.exists():
        return (
            docs_dir,
            f"已忽略不存在的知识库子目录: {normalized}；已回退到知识库根目录搜索。",
        )
    if not search_dir.is_dir():
        return None, f"path 不是目录: {normalized}"
    return search_dir, None


class KnowledgeSearch(Tool):
    name = "knowledge_search"
    description = (
        "在本地 SaaS 知识库文档中全文搜索。"
        "不要用它搜索历史工单；历史工单必须使用 ticket_record_search / ticket_record_read。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "path": {
                "type": "string",
                "description": (
                    "限定搜索的知识库子目录（可选）。不要传历史工单路径；"
                    "历史工单记录不在知识库目录里，应使用 ticket_record_search。"
                ),
            },
        },
        "required": ["query"],
    }

    group = "knowledge"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        docs_dir = _resolve_docs_dir()
        if docs_dir is None:
            return ToolResult(
                success=False,
                error=(
                    "知识库目录不存在。检查 cfg.knowledge.docs_dir 或 "
                    "{resources.base_dir}/{resources.knowledge_base_dir} 是否挂载"
                ),
            )

        search_dir, path_error = _resolve_search_dir(docs_dir, args.get("path"))
        if path_error and search_dir is None:
            return ToolResult(success=False, error=path_error)
        if search_dir is None:
            return ToolResult(success=False, error="知识库目录解析失败")

        if "query" not in args:
            return ToolResult(success=False, error="query is required")

        query = args["query"]
        if not isinstance(query, str):
            return ToolResult(success=False, error="query must be a string")
        query = query.strip()
        if not query:
            return ToolResult(success=False, error="query is empty")

        output = await _search_with_rg(search_dir, query)
        if output is None:
            output = await _search_with_grep(search_dir, query)
        if output is None:
            output = await asyncio.to_thread(_search_with_python, search_dir, query)

        if not output:
            output = f"未找到匹配「{query}」的内容"

        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[:_MAX_OUTPUT_CHARS] + "\n\n... (结果过多，已截断)"

        if path_error:
            output = f"{path_error}\n\n{output}"

        return ToolResult(output=output)


async def _search_with_rg(search_dir: Path, query: str) -> str | None:
    """使用 ripgrep 搜索；不可用时返回 None"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "rg",
            "--ignore-case",
            "--fixed-strings",
            "--max-count", "5",
            "--context", str(_CONTEXT_LINES),
            "-g", "*.md",
            "-g", "*.json",
            query,
            str(search_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=_SEARCH_COMMAND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return None
        if proc.returncode == 1:
            return ""
        if proc.returncode != 0:
            return None
        return stdout.decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return None


async def _search_with_grep(search_dir: Path, query: str) -> str | None:
    """使用 grep 搜索；不可用时返回 None"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep",
            "-r", "-i", "-n", "-F",
            "--include=*.md", "--include=*.json",
            "-C", str(_CONTEXT_LINES),
            query,
            str(search_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=_SEARCH_COMMAND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return None
        if proc.returncode == 1:
            return ""
        if proc.returncode != 0:
            return None
        return stdout.decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return None


def _search_with_python(search_dir: Path, query: str) -> str:
    """纯 Python fallback：当 rg 和 grep 均不可用时使用"""
    try:
        regex = re.compile(re.escape(query), re.IGNORECASE)
    except re.error:
        regex = re.compile(re.escape(query), re.IGNORECASE)

    results: list[str] = []
    match_count = 0

    for filepath in sorted(search_dir.rglob("*")):
        if not filepath.is_file() or filepath.suffix not in _SEARCH_EXTENSIONS:
            continue
        try:
            lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        rel = filepath.relative_to(search_dir)
        for i, line in enumerate(lines):
            if not regex.search(line):
                continue
            match_count += 1
            if match_count > _MAX_MATCHES:
                results.append(f"\n... 超过 {_MAX_MATCHES} 条匹配，已截断")
                return "\n".join(results)

            start = max(0, i - _CONTEXT_LINES)
            end = min(len(lines), i + _CONTEXT_LINES + 1)
            block = [f"{rel}:{j + 1}:{lines[j]}" for j in range(start, end)]
            results.append("\n".join(block))

    return "\n--\n".join(results)


class KnowledgeRead(Tool):
    name = "knowledge_read"
    description = "读取知识库中指定文件的完整内容"
    parameters = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "相对于知识库根目录的文件路径",
            },
        },
        "required": ["filepath"],
    }

    group = "knowledge"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        docs_dir = _resolve_docs_dir()
        if docs_dir is None:
            return ToolResult(success=False, error="知识库目录不存在")
        docs_dir = docs_dir.resolve()

        if "filepath" not in args:
            return ToolResult(success=False, error="filepath is required")

        filepath = args["filepath"]
        if not isinstance(filepath, str):
            return ToolResult(success=False, error="filepath must be a string")
        filepath = filepath.strip()
        if not filepath:
            return ToolResult(success=False, error="filepath is empty")

        target = (docs_dir / filepath).resolve()

        try:
            target.relative_to(docs_dir)
        except ValueError:
            return ToolResult(success=False, error="路径超出知识库范围")

        if not target.exists():
            return ToolResult(success=False, error=f"文件不存在: {filepath}")
        if not target.is_file():
            return ToolResult(success=False, error=f"filepath is not a file: {filepath}")

        try:
            content = target.read_text(encoding="utf-8")
            if len(content) > 50000:
                content = content[:50000] + "\n\n... (文件过大，已截断)"
            return ToolResult(output=content)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
