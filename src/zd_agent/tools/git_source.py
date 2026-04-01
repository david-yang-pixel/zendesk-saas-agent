"""源码工具组：通过 git 命令按版本读取和搜索仓库代码

当版本引用无法解析时，自动触发 git fetch 重试一次。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.retry import get_retry_policy, retry_async
from zd_agent.repos import fetch_repo, list_tags, resolve_repo
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="tools.git_source")

_REF_NOT_FOUND_MARKERS = (
    "unknown revision",
    "not a valid object name",
    "bad revision",
    "invalid object name",
    "unable to resolve revision",
)

_COMPLEX_RE_PATTERN = re.compile(r"(\(\?[!=<]|\\[bBdDwWsS]|\{[\d,]+\}|[|()]{3,})")


def _is_complex_regex(pattern: str) -> bool:
    """检测正则是否过于复杂，git grep 的 POSIX regex 可能无法处理"""
    if _COMPLEX_RE_PATTERN.search(pattern):
        return True
    special = sum(1 for c in pattern if c in r"()[]{}|+*?^$\\")
    return special > 8


async def _run_git(repo_dir: str, *args: str, max_output: int = 30000) -> tuple[int, str, str]:
    """在指定仓库目录执行 git 命令"""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace")
    if len(out) > max_output:
        out = out[:max_output] + "\n\n... (输出过长，已截断)"
    return proc.returncode or 0, out, stderr.decode("utf-8", errors="replace")


def _is_ref_not_found(err: str) -> bool:
    """检查 git 错误是否因为版本引用不存在"""
    err_lower = err.lower()
    return any(marker in err_lower for marker in _REF_NOT_FOUND_MARKERS)


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
    description = "读取指定仓库在特定版本（tag/branch）下某个文件的内容"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名（如 dify、dify-enterprise）",
            },
            "ref": {
                "type": "string",
                "description": "版本引用（tag 或 branch，如 v1.3.0、main）",
            },
            "filepath": {
                "type": "string",
                "description": "文件路径（相对于仓库根目录）",
            },
        },
        "required": ["repo", "ref", "filepath"],
    }

    group = "source_code"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo_dir = resolve_repo(args["repo"])
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {args['repo']}")

        ref = args["ref"]
        filepath = args["filepath"]
        code, out, err = await _run_git(str(repo_dir), "show", f"{ref}:{filepath}")

        # 版本不存在时自动 fetch 重试
        if code != 0 and _is_ref_not_found(err):
            if await _auto_fetch_repo(args["repo"]):
                code, out, err = await _run_git(str(repo_dir), "show", f"{ref}:{filepath}")

        if code != 0:
            return ToolResult(success=False, error=f"git show 失败: {err.strip()}")
        return ToolResult(output=out)


class GitGrep(Tool):
    name = "git_grep"
    description = "在指定仓库的特定版本中搜索代码，返回匹配的文件和行"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名",
            },
            "ref": {
                "type": "string",
                "description": "版本引用（tag 或 branch）",
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
        "required": ["repo", "ref", "pattern"],
    }

    group = "source_code"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo_dir = resolve_repo(args["repo"])
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {args['repo']}")

        pattern = args["pattern"]

        # 如果正则过于复杂（含多个特殊量词/断言），回退为固定字符串搜索
        use_fixed = len(pattern) > 120 or _is_complex_regex(pattern)
        grep_flag = "-F" if use_fixed else "-e"

        git_args = [
            "grep", "-n", "--break", "--heading", "-i",
            grep_flag, pattern, args["ref"],
        ]
        if glob := args.get("glob"):
            git_args.extend(["--", glob])

        code, out, err = await _run_git(str(repo_dir), *git_args)

        # 版本不存在时自动 fetch 重试
        if code != 0 and code != 1 and _is_ref_not_found(err):
            if await _auto_fetch_repo(args["repo"]):
                code, out, err = await _run_git(str(repo_dir), *git_args)

        # 正则语法错误时，自动回退为固定字符串重试
        if code != 0 and code != 1 and not use_fixed and "invalid" in err.lower():
            git_args_fixed = [
                "grep", "-n", "--break", "--heading", "-i",
                "-F", pattern, args["ref"],
            ]
            if glob := args.get("glob"):
                git_args_fixed.extend(["--", glob])
            code, out, err = await _run_git(str(repo_dir), *git_args_fixed)

        if code == 1:
            return ToolResult(output=f"未找到匹配「{pattern}」的代码")
        if code != 0:
            return ToolResult(success=False, error=f"git grep 失败: {err.strip()}")
        return ToolResult(output=out)


class GitLog(Tool):
    name = "git_log"
    description = "查看仓库的提交历史"
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名",
            },
            "ref": {
                "type": "string",
                "description": "版本引用（tag 或 branch）",
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
        "required": ["repo", "ref"],
    }

    group = "source_code"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        repo_dir = resolve_repo(args["repo"])
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {args['repo']}")

        n = str(args.get("n", 20))
        git_args = [
            "log", args["ref"],
            f"-{n}",
            "--oneline", "--decorate",
        ]
        if filepath := args.get("filepath"):
            git_args.extend(["--", filepath])

        code, out, err = await _run_git(str(repo_dir), *git_args)

        if code != 0 and _is_ref_not_found(err):
            if await _auto_fetch_repo(args["repo"]):
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
        repo_dir = resolve_repo(args["repo"])
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {args['repo']}")

        git_args = ["diff", args["ref1"], args["ref2"], "--stat"]
        if filepath := args.get("filepath"):
            git_args = ["diff", args["ref1"], args["ref2"], "--", filepath]

        code, out, err = await _run_git(str(repo_dir), *git_args)

        if code != 0 and _is_ref_not_found(err):
            if await _auto_fetch_repo(args["repo"]):
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
        repo_name = args["repo"]
        repo_dir = resolve_repo(repo_name)
        if repo_dir is None:
            return ToolResult(success=False, error=f"仓库未找到: {repo_name}")

        limit = max(1, min(int(args.get("limit", 30)), 200))
        tags = await asyncio.to_thread(list_tags, repo_name)
        if not tags:
            return ToolResult(output=f"仓库 {repo_name} 没有可用 tag")

        shown = tags[:limit]
        lines = [f"仓库 {repo_name} 的 tag（共 {len(tags)} 个，显示前 {len(shown)} 个）:"]
        lines.extend(f"- {tag}" for tag in shown)
        if len(tags) > limit:
            lines.append(f"- ... 还有 {len(tags) - limit} 个未显示")
        return ToolResult(output="\n".join(lines))
