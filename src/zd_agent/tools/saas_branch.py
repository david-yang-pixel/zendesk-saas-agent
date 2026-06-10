"""SaasBranchSwitch: switch a SaaS repo to a given branch (fetch + checkout)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


def _resolve_repo_name(name: str, aliases: dict[str, str]) -> str:
    """Apply alias resolution. If name isn't in aliases, return as-is."""
    return aliases.get(name.lower(), name)


def _coerce_string_arg(args: dict[str, Any], name: str) -> str:
    value = args.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value.strip()


async def _run_git(
    *args: str, cwd: Path, timeout: float = 60.0,
) -> tuple[int, str, str]:
    """Run a git command, return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


class SaasBranchSwitch(Tool):
    name = "saas_branch_switch"
    description = (
        "切换指定 SaaS 仓库到指定分支（等价于 git fetch + git checkout）。"
        "repo 支持别名（例如 d=dify, bill=dify-billing, mb=dify-marketplace-backend, "
        "mf=dify-marketplace-frontend, deploy=saas-deploy）。"
        "用于跟进 SaaS 线上发布：发布新分支后可立即切换代码到对应版本。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库名或别名",
            },
            "branch": {
                "type": "string",
                "description": "要切换到的分支（如 hotfix/1.11.2-fix3）",
            },
        },
        "required": ["repo", "branch"],
    }
    group = "always"
    concurrency_safe = False  # same repo checkout races are unsafe

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        try:
            repo_in = _coerce_string_arg(args, "repo")
            branch = _coerce_string_arg(args, "branch")
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        if not repo_in:
            return ToolResult(success=False, error="repo is empty")
        if not branch:
            return ToolResult(success=False, error="branch is empty")

        cfg = get_config()
        canonical = _resolve_repo_name(repo_in, cfg.repos.aliases or {})

        base_dir = Path(cfg.repos.base_dir)
        repo_dir = base_dir / canonical
        try:
            repo_dir_resolved = repo_dir.resolve()
            base_dir_resolved = base_dir.resolve()
            repo_dir_resolved.relative_to(base_dir_resolved)
        except ValueError:
            return ToolResult(
                success=False,
                error=f"repo path outside base_dir: {canonical}",
            )
        except Exception:
            return ToolResult(success=False, error="path resolution failed")
        if not repo_dir.exists() or not (repo_dir / ".git").exists():
            return ToolResult(
                success=False,
                error=f"repo directory not a git repo: {repo_dir}",
            )

        # git fetch
        rc, out, err = await _run_git(
            "fetch", "origin", f"{branch}:{branch}", "--force",
            cwd=repo_dir,
        )
        if rc != 0:
            # fetch may fail if branch already exists locally and force isn't applied —
            # try plain `git fetch origin <branch>` as fallback
            rc2, out2, err2 = await _run_git(
                "fetch", "origin", branch, cwd=repo_dir,
            )
            if rc2 != 0:
                return ToolResult(
                    success=False,
                    error=f"git fetch failed: {err.strip() or err2.strip()}",
                )

        # git checkout
        rc, out, err = await _run_git("checkout", branch, cwd=repo_dir)
        if rc != 0:
            return ToolResult(
                success=False,
                error=f"git checkout failed: {err.strip()}",
            )

        # Capture current commit for confirmation
        rc, sha, err = await _run_git("rev-parse", "HEAD", cwd=repo_dir)
        commit_short = sha.strip()[:8] if rc == 0 else "?"

        msg = f"✓ {canonical} → {branch} @ {commit_short}"
        return ToolResult(output=msg)
