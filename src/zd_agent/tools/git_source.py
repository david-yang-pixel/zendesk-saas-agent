"""源码工具组：通过 git 命令按版本读取和搜索仓库代码

当版本引用无法解析时，自动触发 git fetch 重试一次。
"""

from __future__ import annotations

import asyncio
import re
import json
from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.source_mcp import call_source_tool, source_mcp_enabled
from zd_agent.log import get_logger
from zd_agent.runtime.retry import get_retry_policy, retry_async
from zd_agent.repos import fetch_repo, list_tags, resolve_repo
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="tools.git_source")

_REF_NOT_FOUND_MARKERS = (
    "ambiguous argument",
    "unknown revision",
    "not a valid object name",
    "bad revision",
    "invalid object name",
    "unable to resolve revision",
)

_COMPLEX_RE_PATTERN = re.compile(r"(\(\?[!=<]|\\[bBdDwWsS]|\{[\d,]+\}|[|()]{3,})")
_GIT_COMMAND_TIMEOUT_SECONDS = 60.0


def _coerce_source_ref(args: dict[str, Any]) -> str | ToolResult:
    ref = args.get("ref")
    if ref is None:
        return "runtime"
    if not isinstance(ref, str):
        return ToolResult(success=False, error="ref must be a string")
    return ref.strip() or "runtime"


def _coerce_limit(value: Any, *, default: int = 30, maximum: int = 200) -> int | ToolResult:
    try:
        limit = int(default if value is None else value)
    except (TypeError, ValueError):
        return ToolResult(success=False, error="limit must be an integer")
    return max(1, min(limit, maximum))


def _coerce_required_string(args: dict[str, Any], name: str) -> str | ToolResult:
    value = args.get(name)
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    return value


def _coerce_optional_string(args: dict[str, Any], name: str) -> str | ToolResult | None:
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    return value


def _is_complex_regex(pattern: str) -> bool:
    """检测正则是否过于复杂，git grep 的 POSIX regex 可能无法处理"""
    if _COMPLEX_RE_PATTERN.search(pattern):
        return True
    special = sum(1 for c in pattern if c in r"()[]{}|+*?^$\\")
    return special > 8


def _source_mcp_error(payload: dict[str, Any]) -> ToolResult:
    code = payload.get("code") or "SOURCE_MCP_FAILED"
    error = payload.get("error") or "unknown source MCP error"
    return ToolResult(success=False, error=f"{code}: {error}")


def _format_source_mcp_output(data: Any) -> str:
    if isinstance(data, dict):
        header = _format_source_mcp_header(data)
        if isinstance(data.get("content"), str):
            return header + data["content"]
        if isinstance(data.get("output"), str):
            return header + data["output"]
        if isinstance(data.get("tags"), list):
            tags = data["tags"]
            lines = [
                f"仓库 {data.get('repo', '(unknown)')} 的 tag（共 {data.get('total', len(tags))} 个，显示 {len(tags)} 个）:"
            ]
            lines.extend(f"- {tag}" for tag in tags)
            return "\n".join(lines)
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        status_lines = []
        for item in data:
            repo = item.get("repo", "(unknown)")
            if item.get("cloned"):
                status_lines.append(
                    f"- **{repo}** ✅ (commit: {str(item.get('commit', ''))[:12]}, "
                    f"tags: {item.get('tag_count', 0)}, default: {item.get('default_branch_ref', '')})"
                )
            else:
                status_lines.append(f"- **{repo}** ❌ 未克隆")
        return "\n".join(status_lines)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _format_source_mcp_header(data: dict[str, Any]) -> str:
    repo = data.get("repo")
    ref = data.get("ref")
    commit = str(data.get("commit") or "")
    source = data.get("source")
    if not repo and not ref and not commit:
        return ""
    commit_part = f", commit={commit[:12]}" if commit else ""
    source_part = f", source={source}" if source else ""
    return f"[source-mcp: repo={repo}, ref={ref}{commit_part}{source_part}]\n"


async def _remote_source_tool(name: str, arguments: dict[str, Any]) -> ToolResult | None:
    if not source_mcp_enabled():
        return None
    payload = await call_source_tool(name, arguments)
    if not payload.get("success"):
        return _source_mcp_error(payload)
    return ToolResult(output=_format_source_mcp_output(payload.get("data")))


async def _run_git(repo_dir: str, *args: str, max_output: int = 30000) -> tuple[int, str, str]:
    """在指定仓库目录执行 git 命令"""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        err = stderr.decode("utf-8", errors="replace")
        timeout_msg = f"git command timeout after {_GIT_COMMAND_TIMEOUT_SECONDS:.0f}s"
        if err.strip():
            timeout_msg = f"{timeout_msg}: {err.strip()}"
        return 124, stdout.decode("utf-8", errors="replace"), timeout_msg
    out = stdout.decode("utf-8", errors="replace")
    if len(out) > max_output:
        out = out[:max_output] + "\n\n... (输出过长，已截断)"
    return proc.returncode or 0, out, stderr.decode("utf-8", errors="replace")


def _is_ref_not_found(err: str) -> bool:
    """检查 git 错误是否因为版本引用不存在"""
    err_lower = err.lower()
    return any(marker in err_lower for marker in _REF_NOT_FOUND_MARKERS)


def _format_git_error(command: str, ref: str, err: str) -> str:
    message = err.strip()
    if _is_ref_not_found(message):
        return (
            f"{command} 失败: 无法解析版本引用 {ref!r}。"
            "不要猜测 hotfix/tag/branch 名称；请先调用 list_repo_tags 或 saas_deployed_version "
            f"确认可用 ref 后重试。原始错误: {message}"
        )
    return f"{command} 失败: {message}"


async def _auto_fetch_repo(repo_name: str) -> bool:
    """按需 fetch 仓库，返回是否成功"""
    cfg = get_config()
    for entry in cfg.repos.repositories:
        if entry.name == repo_name:
            logger.info("auto_fetch_triggered", repo=repo_name)

            async def _fetch_once() -> tuple[bool, str]:
                ok, msg = await asyncio.to_thread(fetch_repo, entry)
                if not ok:
                    raise RuntimeError(msg)
                return ok, msg

            try:
                ok, msg = await retry_async(
                    operation="git_fetch_repo",
                    policy=get_retry_policy("git"),
                    func=_fetch_once,
                    should_retry=lambda e: True,
                    log_context={"repo": repo_name},
                )
                logger.info("auto_fetch_success", repo=repo_name, message=msg)
                return ok
            except Exception as e:
                logger.warning("auto_fetch_failed", repo=repo_name, error=str(e))
                return False
    return False


class GitShow(Tool):
    name = "git_show"
    description = "读取指定仓库某个文件内容；默认 ref=runtime，即线上正在跑的代码"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名（如 dify、dify-enterprise）",
            },
            "ref": {
                "type": "string",
                "description": "版本引用（tag/branch/commit）。不填默认 runtime = 线上正在跑的代码",
            },
            "filepath": {
                "type": "string",
                "description": "文件路径（相对于仓库根目录）",
            },
        },
        "required": ["repo", "filepath"],
    }

    group = "source_code"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = _coerce_required_string(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        ref = _coerce_source_ref(args)
        if isinstance(ref, ToolResult):
            return ref
        filepath = _coerce_required_string(args, "filepath")
        if isinstance(filepath, ToolResult):
            return filepath

        remote = await _remote_source_tool(
            "source_read",
            {"repo": repo, "ref": ref, "filepath": filepath},
        )
        if remote is not None:
            return remote

        repo_dir = resolve_repo(repo)
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {repo}")

        code, out, err = await _run_git(str(repo_dir), "show", f"{ref}:{filepath}")

        # 版本不存在时自动 fetch 重试
        if code != 0 and _is_ref_not_found(err):
            if await _auto_fetch_repo(repo):
                code, out, err = await _run_git(str(repo_dir), "show", f"{ref}:{filepath}")

        if code != 0:
            return ToolResult(success=False, error=_format_git_error("git show", ref, err))
        return ToolResult(output=out)


class GitGrep(Tool):
    name = "git_grep"
    description = "在指定仓库中搜索代码；默认 ref=runtime，即线上正在跑的代码"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名",
            },
            "ref": {
                "type": "string",
                "description": "版本引用（tag/branch/commit）。不填默认 runtime = 线上正在跑的代码",
            },
            "pattern": {
                "type": "string",
                "description": "搜索模式（支持正则）",
            },
            "glob": {
                "type": "string",
                "description": "文件 glob 过滤（可选，如 *.py、*.go）",
            },
        },
        "required": ["repo", "pattern"],
    }

    group = "source_code"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = _coerce_required_string(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        ref = _coerce_source_ref(args)
        if isinstance(ref, ToolResult):
            return ref
        pattern = _coerce_required_string(args, "pattern")
        if isinstance(pattern, ToolResult):
            return pattern
        glob = _coerce_optional_string(args, "glob")
        if isinstance(glob, ToolResult):
            return glob

        remote_args: dict[str, Any] = {"repo": repo, "ref": ref, "pattern": pattern}
        if glob:
            remote_args["glob"] = glob
        remote = await _remote_source_tool("source_search", remote_args)
        if remote is not None:
            return remote

        repo_dir = resolve_repo(repo)
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {repo}")

        # 如果正则过于复杂（含多个特殊量词/断言），回退为固定字符串搜索
        use_fixed = len(pattern) > 120 or _is_complex_regex(pattern)
        grep_flag = "-F" if use_fixed else "-e"

        git_args = [
            "grep", "-n", "--break", "--heading", "-i",
            grep_flag, pattern, ref,
        ]
        if glob:
            git_args.extend(["--", glob])

        code, out, err = await _run_git(str(repo_dir), *git_args)

        # 版本不存在时自动 fetch 重试
        if code != 0 and code != 1 and _is_ref_not_found(err):
            if await _auto_fetch_repo(repo):
                code, out, err = await _run_git(str(repo_dir), *git_args)

        # 正则语法错误时，自动回退为固定字符串重试
        if code != 0 and code != 1 and not use_fixed and "invalid" in err.lower():
            git_args_fixed = [
                "grep", "-n", "--break", "--heading", "-i",
                "-F", pattern, ref,
            ]
            if glob:
                git_args_fixed.extend(["--", glob])
            code, out, err = await _run_git(str(repo_dir), *git_args_fixed)

        if code == 1:
            return ToolResult(output=f"未找到匹配「{pattern}」的代码")
        if code != 0:
            return ToolResult(success=False, error=_format_git_error("git grep", ref, err))
        return ToolResult(output=out)


class GitLog(Tool):
    name = "git_log"
    description = "查看仓库提交历史；默认 ref=runtime，即线上正在跑的代码"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名",
            },
            "ref": {
                "type": "string",
                "description": "版本引用（tag/branch/commit）。不填默认 runtime = 线上正在跑的代码",
            },
            "filepath": {
                "type": "string",
                "description": "限定到特定文件的历史（可选）",
            },
            "n": {
                "type": "integer",
                "description": "显示最近 N 条提交（默认 20）",
            },
        },
        "required": ["repo"],
    }

    group = "source_code"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = _coerce_required_string(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        ref = _coerce_source_ref(args)
        if isinstance(ref, ToolResult):
            return ref
        filepath = _coerce_optional_string(args, "filepath")
        if isinstance(filepath, ToolResult):
            return filepath
        limit = _coerce_limit(args.get("n"), default=20)
        if isinstance(limit, ToolResult):
            return limit

        remote_args: dict[str, Any] = {"repo": repo, "ref": ref, "n": limit}
        if filepath:
            remote_args["filepath"] = filepath
        remote = await _remote_source_tool("source_log", remote_args)
        if remote is not None:
            return remote

        repo_dir = resolve_repo(repo)
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {repo}")

        git_args = [
            "log", ref,
            f"-{limit}",
            "--oneline", "--decorate",
        ]
        if filepath:
            git_args.extend(["--", filepath])

        code, out, err = await _run_git(str(repo_dir), *git_args)

        if code != 0 and _is_ref_not_found(err):
            if await _auto_fetch_repo(repo):
                code, out, err = await _run_git(str(repo_dir), *git_args)

        if code != 0:
            return ToolResult(success=False, error=f"git log 失败: {err.strip()}")
        return ToolResult(output=out)


class GitDiff(Tool):
    name = "git_diff"
    description = "比较仓库两个版本之间的代码差异"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名",
            },
            "ref1": {
                "type": "string",
                "description": "起始版本（tag 或 branch）",
            },
            "ref2": {
                "type": "string",
                "description": "目标版本（tag 或 branch）",
            },
            "filepath": {
                "type": "string",
                "description": "限定到特定文件（可选）",
            },
        },
        "required": ["repo", "ref1", "ref2"],
    }

    group = "source_code"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo = _coerce_required_string(args, "repo")
        if isinstance(repo, ToolResult):
            return repo
        ref1 = _coerce_required_string(args, "ref1")
        if isinstance(ref1, ToolResult):
            return ref1
        ref2 = _coerce_required_string(args, "ref2")
        if isinstance(ref2, ToolResult):
            return ref2
        filepath = _coerce_optional_string(args, "filepath")
        if isinstance(filepath, ToolResult):
            return filepath

        remote_args: dict[str, Any] = {"repo": repo, "ref1": ref1, "ref2": ref2}
        if filepath:
            remote_args["filepath"] = filepath
        remote = await _remote_source_tool("source_diff", remote_args)
        if remote is not None:
            return remote

        repo_dir = resolve_repo(repo)
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {repo}")

        git_args = ["diff", ref1, ref2, "--stat"]
        if filepath:
            git_args = ["diff", ref1, ref2, "--", filepath]

        code, out, err = await _run_git(str(repo_dir), *git_args)

        if code != 0 and _is_ref_not_found(err):
            if await _auto_fetch_repo(repo):
                code, out, err = await _run_git(str(repo_dir), *git_args)

        if code != 0:
            return ToolResult(success=False, error=f"git diff 失败: {err.strip()}")
        return ToolResult(output=out if out else "两个版本之间没有差异")


class ListRepos(Tool):
    name = "list_repos"
    description = "列出所有可用的源码仓库及其本地状态（是否已克隆、tag 数量等）"
    parameters = {
        "type": "object",
        "properties": {},
    }

    group = "source_code"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        remote = await _remote_source_tool("repo_status", {})
        if remote is not None:
            return remote

        from zd_agent.repos import get_repo_status

        cfg = get_config()
        lines = []

        for entry in cfg.repos.repositories:
            status = get_repo_status(entry)
            if status.cloned:
                mode = "bare" if status.is_bare else "normal"
                lines.append(
                    f"- **{status.name}** ✅ ({mode}, tags: {status.tag_count})"
                )
            else:
                lines.append(f"- **{status.name}** ❌ 未克隆")

        return ToolResult(output="\n".join(lines))


class ListRepoTags(Tool):
    name = "list_repo_tags"
    description = "列出指定仓库的真实 tag 列表，帮助确认可用版本，避免错误回退到 main"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名（如 dify、dify-enterprise）",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回多少个 tag（默认 30）",
            },
        },
        "required": ["repo"],
    }

    group = "source_code"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo_name = _coerce_required_string(args, "repo")
        if isinstance(repo_name, ToolResult):
            return repo_name
        limit = _coerce_limit(args.get("limit"))
        if isinstance(limit, ToolResult):
            return limit

        remote = await _remote_source_tool(
            "list_repo_tags",
            {"repo": repo_name, "limit": limit},
        )
        if remote is not None:
            return remote

        repo_dir = resolve_repo(repo_name)
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {repo_name}")
        tags = await asyncio.to_thread(list_tags, repo_name)
        if not tags:
            return ToolResult(output=f"仓库 {repo_name} 没有可用 tag")

        shown = tags[:limit]
        lines = [f"仓库 {repo_name} 的 tag（共 {len(tags)} 个，显示前 {len(shown)} 个）:"]
        lines.extend(f"- {tag}" for tag in shown)
        if len(tags) > limit:
            lines.append(f"- ... 还有 {len(tags) - limit} 个未显示")
        return ToolResult(output="\n".join(lines))
