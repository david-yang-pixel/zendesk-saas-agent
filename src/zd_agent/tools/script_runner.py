"""环境工具组：排查脚本执行 + sandbox 命令执行"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

SCRIPTS_DIR = Path("scripts")

# 允许执行的脚本白名单（文件名）
ALLOWED_SCRIPTS: set[str] = {
    "check-plugin-runtime.sh",
}


class RunScript(Tool):
    name = "run_script"
    description = "执行 scripts/ 目录下的排查脚本（仅限白名单内的脚本）"
    parameters = {
        "type": "object",
        "properties": {
            "script_name": {
                "type": "string",
                "description": "脚本文件名（如 check-plugin-runtime.sh）",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "传给脚本的参数列表（可选）",
            },
        },
        "required": ["script_name"],
    }

    group = "environment"
    concurrency_safe = False

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        if "script_name" not in args:
            return ToolResult(success=False, error="script_name is required")
        script_name = args["script_name"]
        if not isinstance(script_name, str):
            return ToolResult(success=False, error="script_name must be a string")
        script_name = script_name.strip()
        if not script_name:
            return ToolResult(success=False, error="script_name is empty")

        if script_name not in ALLOWED_SCRIPTS:
            return ToolResult(
                success=False,
                error=f"脚本不在白名单中: {script_name}。允许的脚本: {', '.join(sorted(ALLOWED_SCRIPTS))}",
            )

        script_path = SCRIPTS_DIR / script_name
        if not script_path.exists():
            return ToolResult(success=False, error=f"脚本文件不存在: {script_path}")

        script_args = args.get("args") or []
        if not isinstance(script_args, list) or not all(isinstance(a, str) for a in script_args):
            return ToolResult(success=False, error="args must be a list of strings")
        cmd_args = ["bash", str(script_path)] + script_args

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(session.sandbox_dir) if session.sandbox_dir else None,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            output = stdout.decode("utf-8", errors="replace")
            err_output = stderr.decode("utf-8", errors="replace")

            result_text = f"退出码: {proc.returncode}\n\n"
            if output:
                result_text += f"--- stdout ---\n{output}\n"
            if err_output:
                result_text += f"--- stderr ---\n{err_output}\n"

            return ToolResult(
                success=proc.returncode == 0,
                output=result_text[:10000],
                error=None if proc.returncode == 0 else f"脚本退出码: {proc.returncode}",
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, error="脚本执行超时（120 秒）")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class SandboxExec(Tool):
    name = "sandbox_exec"
    description = "在 sandbox 目录内执行受限命令（禁止网络请求和修改共享仓库）"
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令",
            },
            "cwd": {
                "type": "string",
                "description": "工作目录（相对于 sandbox 根目录，可选）",
            },
        },
        "required": ["command"],
    }

    group = "environment"
    requires_sandbox = True
    concurrency_safe = False

    # 白名单：只允许这些命令开头的指令
    ALLOWED_PREFIXES = {
        "ls", "cat", "head", "tail", "wc", "find", "grep", "rg",
        "file", "stat", "du", "diff", "tree", "echo", "pwd",
        "git", "python -c", "python3 -c",
        "zendesk",
    }

    BLOCKED_PATTERNS = {
        "rm ", "rm\t", "rmdir", "mv ", "cp ",
        "curl", "wget", "nc ", "ncat", "ssh ", "scp ", "rsync",
        "git push", "git commit", "git reset", "git clean",
        "chmod", "chown", "sudo", "su ",
        "> ", ">> ", "| sh", "| bash", "xargs",
        "import os", "import subprocess", "import shutil",
        "__import__", "exec(", "eval(",
    }

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        if not session.sandbox_dir:
            return ToolResult(success=False, error="Sandbox 未初始化")

        if "command" not in args:
            return ToolResult(success=False, error="command is required")
        raw_command = args["command"]
        if not isinstance(raw_command, str):
            return ToolResult(success=False, error="command must be a string")
        command = raw_command.strip()
        if not command:
            return ToolResult(success=False, error="command is empty")
        cmd_lower = command.lower()

        # 黑名单检查：包含危险模式直接拒绝
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in cmd_lower:
                return ToolResult(success=False, error=f"命令被禁止: 包含 '{pattern}'")

        # 白名单检查：命令必须以允许的前缀开头
        cmd_base = cmd_lower.split()[0] if cmd_lower.split() else ""
        allowed = any(
            cmd_lower.startswith(prefix) for prefix in self.ALLOWED_PREFIXES
        ) or cmd_base in self.ALLOWED_PREFIXES
        if not allowed:
            return ToolResult(
                success=False,
                error=f"命令不在白名单中: '{cmd_base}'。允许: {', '.join(sorted(self.ALLOWED_PREFIXES))}",
            )

        work_dir = session.sandbox_dir
        if cwd := args.get("cwd"):
            if not isinstance(cwd, str):
                return ToolResult(success=False, error="cwd must be a string")
            work_dir = session.sandbox_dir / cwd
            if not _is_within(work_dir, session.sandbox_dir):
                return ToolResult(success=False, error="工作目录超出 sandbox 范围")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            output = stdout.decode("utf-8", errors="replace")
            err_output = stderr.decode("utf-8", errors="replace")

            result_text = f"退出码: {proc.returncode}\n"
            if output:
                result_text += f"\n{output}"
            if err_output:
                result_text += f"\n--- stderr ---\n{err_output}"

            return ToolResult(
                success=proc.returncode == 0,
                output=result_text[:10000],
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, error="命令执行超时（60 秒）")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def _is_within(child: Path, parent: Path) -> bool:
    """安全的路径包含检查，避免前缀匹配误判"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
