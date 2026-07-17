"""Sandbox 工具组：worktree 创建、文件读写、目录浏览与全文搜索"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from zd_agent.repos import resolve_repo
from zd_agent.integrations.sandbox_worker import call_sandbox_worker_tool
from zd_agent.runtime.retry import get_retry_policy, retry_async
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
_IMAGE_MAGIC_PREFIXES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"BM",
)


def _coerce_int_arg(
    args: dict[str, Any],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int | ToolResult:
    try:
        value = int(args.get(name, default))
    except (TypeError, ValueError):
        return ToolResult(
            success=False,
            error=f"{name} must be an integer in [{minimum}, {maximum}]",
        )
    return max(minimum, min(value, maximum))


def _coerce_required_string_arg(args: dict[str, Any], name: str) -> str | ToolResult:
    value = args.get(name)
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    return value


def _coerce_optional_string_arg(args: dict[str, Any], name: str) -> str | ToolResult | None:
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    return value


def _worker_result(payload: dict[str, Any]) -> ToolResult:
    if payload.get("success"):
        output = payload.get("output")
        if isinstance(output, str):
            return ToolResult(output=output)
        data = payload.get("data")
        return ToolResult(output=data if isinstance(data, str) else str(data or "ok"))
    code = payload.get("code") or "UNKNOWN"
    error = payload.get("error") or "unknown error"
    return ToolResult(success=False, error=f"Sandbox worker failed [{code}]: {error}")


def _suggest_sandbox_relative_path(raw_path: str, session: AgentSession) -> str:
    normalized = raw_path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    ticket_id = str(session.ticket_id)
    if ticket_id in parts:
        parts = parts[parts.index(ticket_id) + 1:]
    return "/".join(parts) or "attachments/<filename>"


def _validate_sandbox_relative_path(
    raw_path: str,
    session: AgentSession,
    *,
    argument: str,
    allow_empty: bool = False,
) -> ToolResult | None:
    cleaned = raw_path.strip()
    if not cleaned and allow_empty:
        return None
    suggested = _suggest_sandbox_relative_path(cleaned, session)
    if Path(cleaned).is_absolute() or _WINDOWS_ABSOLUTE_PATH_RE.match(cleaned):
        return ToolResult(
            success=False,
            error=(
                f"{argument} must be relative to the current ticket sandbox root. "
                f"Use '{suggested}', not an absolute path."
            ),
        )
    parts = Path(cleaned).parts
    if parts and parts[0] == str(session.ticket_id):
        return ToolResult(
            success=False,
            error=(
                f"The sandbox root is already ticket #{session.ticket_id}; "
                f"do not repeat the ticket ID. Use '{suggested}'."
            ),
        )
    return None


def _is_image_path(target: Path, raw_path: str) -> bool:
    if Path(raw_path).suffix.lower() in _IMAGE_SUFFIXES:
        return True
    try:
        with target.open("rb") as handle:
            prefix = handle.read(16)
    except OSError:
        return False
    return prefix.startswith(_IMAGE_MAGIC_PREFIXES) or (
        len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP"
    )


async def _call_worker(
    name: str,
    args: dict[str, Any],
    session: AgentSession,
) -> ToolResult:
    payload = await call_sandbox_worker_tool(name, args, session=session)
    return _worker_result(payload)


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

        repo = _coerce_required_string_arg(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        ref = _coerce_required_string_arg(args, "ref")
        if isinstance(ref, ToolResult):
            return ref

        repo_dir = resolve_repo(repo)
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {repo}")

        # resolve() 消除 macOS /tmp → /private/tmp 符号链接差异
        worktree_path = (session.sandbox_dir / "worktree" / repo).resolve()
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
                    "--force",
                    "--detach",
                    str(worktree_path),
                    ref,
                )
                if code != 0:
                    raise RuntimeError((err or out).strip())

            def _should_retry(e: Exception) -> bool:
                msg = str(e).lower()
                # ref 不存在时不重试，避免浪费
                if "invalid reference" in msg or "unknown revision" in msg:
                    return False
                return True

            await retry_async(
                operation="sandbox_create_worktree",
                policy=get_retry_policy("sandbox"),
                func=_create_worktree,
                should_retry=_should_retry,
                log_context={"repo": repo, "ref": ref},
            )

            return ToolResult(
                output=f"已创建 worktree: {worktree_path}\n版本: {ref}\n仓库: {repo}"
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

        filepath = _coerce_required_string_arg(args, "filepath")
        if isinstance(filepath, ToolResult):
            return filepath
        path_error = _validate_sandbox_relative_path(
            filepath,
            session,
            argument="filepath",
        )
        if path_error is not None:
            return path_error
        content = _coerce_required_string_arg(args, "content")
        if isinstance(content, ToolResult):
            return content

        sandbox_root = _sandbox_root(session)
        target = (sandbox_root / filepath).resolve()
        if not _is_within(target, sandbox_root):
            return ToolResult(success=False, error="路径超出 sandbox 范围")

        return await _call_worker(self.name, args, session)


class SandboxRead(Tool):
    name = "sandbox_read"
    description = (
        "仅以 UTF-8 文本读取 sandbox 文件，支持按行号读取片段。"
        "不得用于 PNG/JPEG/GIF/WebP 等图片；工单图片先调用 zendesk_get_attachments，"
        "下载成功后会在下一轮自动注入视觉输入。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": (
                    "相对于当前工单 sandbox 根目录的文本路径，例如 "
                    "attachments/error.log；不要加 ticket_id，不要传绝对路径"
                ),
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

        filepath = _coerce_required_string_arg(args, "filepath")
        if isinstance(filepath, ToolResult):
            return filepath
        path_error = _validate_sandbox_relative_path(
            filepath,
            session,
            argument="filepath",
        )
        if path_error is not None:
            return path_error

        sandbox_root = _sandbox_root(session)
        target = (sandbox_root / filepath).resolve()
        if not _is_within(target, sandbox_root):
            return ToolResult(success=False, error="路径超出 sandbox 范围")
        if _is_image_path(target, filepath):
            return ToolResult(
                success=False,
                error=(
                    "sandbox_read is text-only and cannot decode image attachments. "
                    "Call zendesk_get_attachments; downloaded images are queued for "
                    "automatic vision injection in the next model round."
                ),
            )

        if "start_line" in args:
            start_line_check = _coerce_int_arg(
                args,
                "start_line",
                1,
                minimum=1,
                maximum=1_000_000_000,
            )
            if isinstance(start_line_check, ToolResult):
                return start_line_check
        if "end_line" in args:
            end_line_check = _coerce_int_arg(
                args,
                "end_line",
                1,
                minimum=1,
                maximum=1_000_000_000,
            )
            if isinstance(end_line_check, ToolResult):
                return end_line_check

        return await _call_worker(self.name, args, session)


class SandboxList(Tool):
    name = "sandbox_list"
    description = (
        "列出当前工单 sandbox 或 worktree 中的目录/文件。路径相对于 sandbox 根目录；"
        "附件目录直接写 attachments，不要加 ticket_id 或绝对路径。"
    )
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
        raw_path = _coerce_optional_string_arg(args, "path")
        if isinstance(raw_path, ToolResult):
            return raw_path
        path_error = _validate_sandbox_relative_path(
            raw_path or "",
            session,
            argument="path",
            allow_empty=True,
        )
        if path_error is not None:
            return path_error
        target = _resolve_sandbox_path(session, raw_path or "")
        if isinstance(target, ToolResult):
            return target

        max_depth = _coerce_int_arg(args, "max_depth", 2, minimum=0, maximum=6)
        if isinstance(max_depth, ToolResult):
            return max_depth
        limit = _coerce_int_arg(args, "limit", 200, minimum=1, maximum=1000)
        if isinstance(limit, ToolResult):
            return limit

        return await _call_worker(
            self.name,
            {
                "path": raw_path or "",
                "max_depth": max_depth,
                "limit": limit,
            },
            session,
        )


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
        pattern = _coerce_required_string_arg(args, "pattern")
        if isinstance(pattern, ToolResult):
            return pattern
        raw_path = _coerce_optional_string_arg(args, "path")
        if isinstance(raw_path, ToolResult):
            return raw_path
        path_error = _validate_sandbox_relative_path(
            raw_path or "",
            session,
            argument="path",
            allow_empty=True,
        )
        if path_error is not None:
            return path_error
        glob = _coerce_optional_string_arg(args, "glob")
        if isinstance(glob, ToolResult):
            return glob

        target = _resolve_sandbox_path(session, raw_path or "")
        if isinstance(target, ToolResult):
            return target

        return await _call_worker(
            self.name,
            {"pattern": pattern, "path": raw_path or "", "glob": glob},
            session,
        )


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


async def _prune_and_cleanup_worktree(repo_dir: str, worktree_path: Path) -> None:
    """清理残留的 worktree 注册和物理目录，确保后续 worktree add 不会冲突。

    处理流程：
    1. git worktree unlock（解除 locked 状态，prune 不处理 locked worktree）
    2. git worktree prune — 清理指向已不存在目录的注册
    3. 如果仍然注册，git worktree remove --force 强制移除
    4. 删除残留的物理目录
    """
    wt_str = str(worktree_path)

    # unlock：locked worktree 不会被 prune 清理，必须先解锁
    await _run_git(repo_dir, "worktree", "unlock", wt_str)
    await _run_git(repo_dir, "worktree", "prune")

    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)

    # 检查是否仍然注册（路径匹配需兼容 /tmp 和 /private/tmp）
    code, out, _ = await _run_git(repo_dir, "worktree", "list", "--porcelain")
    if code == 0:
        for line in out.splitlines():
            if not line.startswith("worktree "):
                continue
            registered = line[9:]
            if registered == wt_str or Path(registered).resolve() == worktree_path:
                await _run_git(repo_dir, "worktree", "unlock", registered)
                await _run_git(repo_dir, "worktree", "remove", "--force", registered)
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
