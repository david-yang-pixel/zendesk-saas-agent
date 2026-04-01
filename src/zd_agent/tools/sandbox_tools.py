"""Sandbox 工具组：worktree 创建、文件读写、目录浏览与全文搜索"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from zd_agent.repos import resolve_repo
from zd_agent.runtime.retry import get_retry_policy, retry_async
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

READ_MAX_CHARS = 12000
READ_MAX_LINES = 220
SEARCH_MAX_MATCHES = 40
SEARCH_MAX_OUTPUT_CHARS = 12000
SEARCH_CONTEXT_LINES = 1


class SandboxCreateWorktree(Tool):
    name = "sandbox_create_worktree"
    description = "在 sandbox 中为指定仓库创建 git worktree（用于需要完整文件系统的排查场景）"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名（如 dify、dify-enterprise）",
            },
            "ref": {
                "type": "string",
                "description": "版本引用（tag 或 branch）",
            },
        },
        "required": ["repo", "ref"],
    }

    group = "sandbox"
    requires_sandbox = True
    concurrency_safe = False

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        if not session.sandbox_dir:
            return ToolResult(success=False, error="Sandbox 未初始化")

        repo_dir = resolve_repo(args["repo"])
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {args['repo']}")

        # resolve() 消除 macOS /tmp → /private/tmp 符号链接差异
        worktree_path = (session.sandbox_dir / "worktree" / args["repo"]).resolve()
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        if worktree_path.exists() and (worktree_path / ".git").exists():
            return ToolResult(output=f"Worktree 已存在: {worktree_path}")

        try:
            async def _create_worktree() -> None:
                await _prune_and_cleanup_worktree(str(repo_dir), worktree_path)

                code, out, err = await _run_git(
                    str(repo_dir),
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree_path),
                    args["ref"],
                )
                if code != 0:
                    raise RuntimeError((err or out).strip())

            await retry_async(
                operation="sandbox_create_worktree",
                policy=get_retry_policy("sandbox"),
                func=_create_worktree,
                should_retry=lambda e: True,
                log_context={"repo": args["repo"], "ref": args["ref"]},
            )

            return ToolResult(
                output=f"已创建 worktree: {worktree_path}\n版本: {args['ref']}\n仓库: {args['repo']}"
            )
        except Exception as e:
            return ToolResult(success=False, error=f"创建 worktree 失败: {e}")


class SandboxWrite(Tool):
    name = "sandbox_write"
    description = "向 sandbox 目录写入文件"
    parameters = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "相对于 sandbox 根目录的文件路径",
            },
            "content": {
                "type": "string",
                "description": "文件内容",
            },
        },
        "required": ["filepath", "content"],
    }

    group = "sandbox"
    requires_sandbox = True
    concurrency_safe = False
    is_write_operation = True

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        if not session.sandbox_dir:
            return ToolResult(success=False, error="Sandbox 未初始化")

        sandbox_root = _sandbox_root(session)
        target = (sandbox_root / args["filepath"]).resolve()
        if not _is_within(target, sandbox_root):
            return ToolResult(success=False, error="路径超出 sandbox 范围")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args["content"], encoding="utf-8")
            return ToolResult(output=f"已写入: {args['filepath']}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class SandboxRead(Tool):
    name = "sandbox_read"
    description = "读取 sandbox 目录中的文件内容，支持按行号读取片段，避免一次返回整份大文件"
    parameters = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "相对于 sandbox 根目录的文件路径",
            },
            "start_line": {
                "type": "integer",
                "description": "起始行号（从 1 开始，可选）",
            },
            "end_line": {
                "type": "integer",
                "description": "结束行号（包含，可选）",
            },
        },
        "required": ["filepath"],
    }

    group = "sandbox"
    requires_sandbox = True

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        if not session.sandbox_dir:
            return ToolResult(success=False, error="Sandbox 未初始化")

        sandbox_root = _sandbox_root(session)
        target = (sandbox_root / args["filepath"]).resolve()
        if not _is_within(target, sandbox_root):
            return ToolResult(success=False, error="路径超出 sandbox 范围")

        if not target.exists():
            return ToolResult(success=False, error=f"文件不存在: {args['filepath']}")

        try:
            content = target.read_text(encoding="utf-8")
            lines = content.splitlines()

            start_line = int(args.get("start_line", 1))
            if start_line < 1:
                start_line = 1
            end_line = int(args.get("end_line", start_line + READ_MAX_LINES - 1))
            end_line = min(max(end_line, start_line), len(lines))

            selected = lines[start_line - 1:end_line]
            numbered = [f"{idx}|{line}" for idx, line in enumerate(selected, start=start_line)]
            output = "\n".join(numbered)

            if len(output) > READ_MAX_CHARS:
                output = output[:READ_MAX_CHARS] + "\n\n... (片段过长，已截断)"

            header = f"文件: {args['filepath']}\n行号: {start_line}-{end_line}/{len(lines)}\n"
            if end_line < len(lines):
                header += "提示: 文件未完整返回；如需更多内容，请继续读取相邻行号区间。\n"
            return ToolResult(output=f"{header}\n{output}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class SandboxList(Tool):
    name = "sandbox_list"
    description = "列出 sandbox 或 worktree 中的目录/文件，帮助浏览完整代码树结构"
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对于 sandbox 根目录的路径；默认列出 sandbox 根目录",
            },
            "max_depth": {
                "type": "integer",
                "description": "递归显示的最大深度（默认 2）",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回多少个条目（默认 200）",
            },
        },
    }

    group = "sandbox"
    requires_sandbox = True

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        target = _resolve_sandbox_path(session, args.get("path", ""))
        if isinstance(target, ToolResult):
            return target

        if not target.exists():
            return ToolResult(success=False, error=f"路径不存在: {args.get('path', '.')}")

        max_depth = max(0, min(int(args.get("max_depth", 2)), 6))
        limit = max(1, min(int(args.get("limit", 200)), 1000))

        lines: list[str] = [f"目录树: {target.relative_to(_sandbox_root(session))}"]
        count = 0

        def walk(path: Path, depth: int) -> None:
            nonlocal count
            if count >= limit or depth > max_depth:
                return

            try:
                entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError as e:
                lines.append(f"[无法读取] {path.name}: {e}")
                return

            for entry in entries:
                if count >= limit:
                    return
                rel = entry.relative_to(target)
                indent = "  " * len(rel.parts[:-1])
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{indent}- {entry.name}{suffix}")
                count += 1
                if entry.is_dir() and depth < max_depth:
                    walk(entry, depth + 1)

        if target.is_file():
            lines.append(f"- {target.name}")
        else:
            walk(target, 0)

        if count >= limit:
            lines.append(f"... 已截断，最多显示 {limit} 个条目")
        return ToolResult(output="\n".join(lines))


class SandboxSearch(Tool):
    name = "sandbox_search"
    description = "在 sandbox 或 worktree 中全文搜索代码/配置，适合在完整代码树里继续定位调用链"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "搜索模式（支持 rg 正则）",
            },
            "path": {
                "type": "string",
                "description": "相对于 sandbox 根目录的搜索路径；默认整个 sandbox",
            },
            "glob": {
                "type": "string",
                "description": "文件过滤模式（可选，如 *.ts、*.go）",
            },
        },
        "required": ["pattern"],
    }

    group = "sandbox"
    requires_sandbox = True
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        target = _resolve_sandbox_path(session, args.get("path", ""))
        if isinstance(target, ToolResult):
            return target

        if not target.exists():
            return ToolResult(success=False, error=f"路径不存在: {args.get('path', '.')}")

        output = await _search_with_rg(target, args["pattern"], args.get("glob"))
        if output is None:
            output = await asyncio.to_thread(
                _search_with_python, target, args["pattern"], args.get("glob")
            )

        if not output:
            return ToolResult(output=f"未找到匹配「{args['pattern']}」的内容")
        trimmed = _trim_search_output(output, args["pattern"])
        return ToolResult(output=trimmed)


def _is_within(child: Path, parent: Path) -> bool:
    """安全的路径包含检查"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _resolve_sandbox_path(session: AgentSession, raw_path: str) -> Path | ToolResult:
    """将相对 sandbox 的路径解析为绝对路径，并校验不越界"""
    if not session.sandbox_dir:
        return ToolResult(success=False, error="Sandbox 未初始化")

    sandbox_root = _sandbox_root(session)
    target = (sandbox_root / raw_path).resolve() if raw_path else sandbox_root
    if not _is_within(target, sandbox_root):
        return ToolResult(success=False, error="路径超出 sandbox 范围")
    return target


def _sandbox_root(session: AgentSession) -> Path:
    """返回归一化后的 sandbox 根目录，避免 /tmp 与 /private/tmp 差异"""
    if not session.sandbox_dir:
        raise ValueError("Sandbox 未初始化")
    return session.sandbox_dir.resolve()


async def _search_with_rg(target: Path, pattern: str, glob: str | None) -> str | None:
    """优先使用 rg 搜索；若 rg 不可用则返回 None 走 Python fallback"""
    cmd = [
        "rg",
        "-n",
        "--hidden",
        "--glob",
        "!.git",
        pattern,
        str(target),
    ]
    if glob:
        cmd[4:4] = ["--glob", glob]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return None

    output = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode == 1:
        return ""
    if proc.returncode != 0:
        raise RuntimeError(f"sandbox_search 失败: {err}")
    return output


async def _prune_and_cleanup_worktree(repo_dir: str, worktree_path: Path) -> None:
    """清理残留的 worktree 注册和物理目录，确保后续 worktree add 不会冲突。

    处理流程：
    1. git worktree prune — 清理指向已不存在目录的注册
    2. git worktree remove --force — 如果仍然注册则强制移除
    3. 删除残留的物理目录
    """
    await _run_git(repo_dir, "worktree", "prune")

    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)

    # prune 后检查是否仍然注册（极端情况：路径存在但状态异常）
    code, out, _ = await _run_git(repo_dir, "worktree", "list", "--porcelain")
    if code == 0:
        wt_str = str(worktree_path)
        for line in out.splitlines():
            if line.startswith("worktree ") and line[9:] == wt_str:
                await _run_git(repo_dir, "worktree", "remove", "--force", wt_str)
                await _run_git(repo_dir, "worktree", "prune")
                break

    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)


async def _run_git(repo_dir: str, *args: str) -> tuple[int, str, str]:
    """在指定仓库目录执行 git 命令"""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _search_with_python(target: Path, pattern: str, glob: str | None) -> str:
    """在无 rg 的环境下，使用 Python 递归搜索文本文件"""
    regex = re.compile(pattern, re.IGNORECASE)
    results: list[str] = []
    max_matches = 400

    if target.is_file():
        paths = [target]
    else:
        paths = [p for p in target.rglob("*") if p.is_file() and ".git" not in p.parts]

    for path in paths:
        if glob and not path.match(glob):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            if regex.search(line):
                results.append(f"{path}:{lineno}:{line}")
                if len(results) >= max_matches:
                    results.append(f"... 已截断，最多显示 {max_matches} 条匹配")
                    return "\n".join(results)

    return "\n".join(results)


def _trim_search_output(output: str, pattern: str) -> str:
    """裁剪搜索输出：限制命中数量和总字符数，逼迫后续用 sandbox_read 精读"""
    lines = [line for line in output.splitlines() if line.strip()]
    shown = lines[:SEARCH_MAX_MATCHES]
    text = "\n".join(shown)
    if len(text) > SEARCH_MAX_OUTPUT_CHARS:
        text = text[:SEARCH_MAX_OUTPUT_CHARS].rstrip() + "\n... (搜索结果过长，已截断)"

    result = (
        f"搜索模式: {pattern}\n"
        f"返回命中: {min(len(lines), SEARCH_MAX_MATCHES)} / {len(lines)}\n"
        "提示: 先挑最相关的文件，再用 sandbox_read 按行号读取局部片段，不要一次性读取整文件。\n\n"
        f"{text}"
    )
    if len(lines) > SEARCH_MAX_MATCHES:
        result += f"\n\n... 还有 {len(lines) - SEARCH_MAX_MATCHES} 条命中未显示"
    return result
