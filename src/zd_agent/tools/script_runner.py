"""环境工具组：排查脚本执行 + sandbox 命令执行"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from zd_agent.integrations.sandbox_worker import (
    call_sandbox_worker_tool,
)
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

# 允许执行的脚本白名单（文件名）
ALLOWED_SCRIPTS: set[str] = {
    "check-plugin-runtime.sh",
}


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


async def _call_worker(
    name: str,
    args: dict[str, Any],
    session: AgentSession,
) -> ToolResult:
    payload = await call_sandbox_worker_tool(name, args, session=session)
    return _worker_result(payload)


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

        script_args = args.get("args") or []
        if not isinstance(script_args, list) or not all(isinstance(a, str) for a in script_args):
            return ToolResult(success=False, error="args must be a list of strings")

        return await _call_worker(
            self.name,
            {"script_name": script_name, "args": script_args},
            session,
        )


class SandboxExec(Tool):
    name = "sandbox_exec"
    description = "在 sandbox 目录内执行受限命令（禁止网络请求和修改共享仓库）"
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的单行命令；禁止换行、管道、重定向、分号、&&、||、命令替换等 shell 控制符",
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
        "cat", "head", "tail", "stat", "echo", "pwd",
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

    SHELL_CONTROL_OPERATORS = {
        ";", "&", "||", "|", "`", "$", ">", "<", "*", "?", "[", "]",
        "~", "{", "}", "(", ")", "!", "\n", "\r",
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

        for operator in self.SHELL_CONTROL_OPERATORS:
            if operator in command:
                return ToolResult(
                    success=False,
                    error=f"command contains forbidden shell control operator: {operator!r}",
                )

        # 黑名单检查：包含危险模式直接拒绝
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in cmd_lower:
                return ToolResult(success=False, error=f"命令被禁止: 包含 '{pattern}'")

        # 白名单检查：命令必须以允许的前缀开头
        cmd_base = cmd_lower.split()[0] if cmd_lower.split() else ""
        allowed = cmd_base in self.ALLOWED_PREFIXES
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

        external_path = _first_external_path_reference(
            command,
            work_dir=work_dir,
            sandbox_dir=session.sandbox_dir,
        )
        if external_path:
            return ToolResult(
                success=False,
                error=f"命令引用了 sandbox 外路径: {external_path}",
            )

        return await _call_worker(
            self.name,
            {"command": command, "cwd": cwd or ""},
            session,
        )


def _is_within(child: Path, parent: Path) -> bool:
    """安全的路径包含检查，避免前缀匹配误判"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


_ABS_PATH_RE = re.compile(r"(?<![\w.-])/(?:[^\s'\";|&<>`$()]+)")


def _first_external_path_reference(
    command: str,
    *,
    work_dir: Path,
    sandbox_dir: Path,
) -> str:
    """Reject obvious path references outside the sandbox root."""
    candidates: set[str] = set(_ABS_PATH_RE.findall(command))
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "invalid shell quoting"

    for token in tokens[1:]:
        value = token.split("=", 1)[1] if token.startswith("--") and "=" in token else token
        if value.startswith("-"):
            continue
        candidates.add(value)

    for candidate in sorted(candidates):
        path = Path(candidate) if candidate.startswith("/") else work_dir / candidate
        if not _is_within(path, sandbox_dir):
            return candidate
    return ""
