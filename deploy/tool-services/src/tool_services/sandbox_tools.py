from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path
from typing import Any

from .mcp import failure, optional_int, optional_str, required_str, success
from .settings import Settings

READ_MAX_CHARS = 12_000
READ_MAX_LINES = 220
SEARCH_MAX_MATCHES = 40
SEARCH_MAX_OUTPUT_CHARS = 12_000
ALLOWED_SCRIPTS = {"check-plugin-runtime.sh"}
ALLOWED_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "find", "grep", "rg",
    "file", "stat", "du", "diff", "tree", "echo", "pwd", "git",
}
BLOCKED_ARGS = {
    "rm", "rmdir", "mv", "cp", "curl", "wget", "nc", "ncat", "ssh", "scp",
    "rsync", "chmod", "chown", "sudo", "su", "xargs",
}

TOOLS = [
    {
        "name": "sandbox_write",
        "description": "Write a UTF-8 file under the ticket sandbox root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "content": {"type": "string"},
                "sandbox_dir": {"type": "string"},
                "ticket_id": {"type": ["string", "integer"]},
            },
            "required": ["filepath", "content"],
        },
    },
    {
        "name": "sandbox_read",
        "description": "Read a UTF-8 file snippet under the ticket sandbox root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "sandbox_dir": {"type": "string"},
                "ticket_id": {"type": ["string", "integer"]},
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "sandbox_list",
        "description": "List files under the ticket sandbox root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_depth": {"type": "integer", "default": 2},
                "limit": {"type": "integer", "default": 200},
                "sandbox_dir": {"type": "string"},
                "ticket_id": {"type": ["string", "integer"]},
            },
        },
    },
    {
        "name": "sandbox_search",
        "description": "Search text under the ticket sandbox root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "sandbox_dir": {"type": "string"},
                "ticket_id": {"type": ["string", "integer"]},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "sandbox_exec",
        "description": "Execute a limited command in the ticket sandbox root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "sandbox_dir": {"type": "string"},
                "ticket_id": {"type": ["string", "integer"]},
            },
            "required": ["command"],
        },
    },
    {
        "name": "run_script",
        "description": "Run an allowlisted diagnostic script against the ticket sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script_name": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "sandbox_dir": {"type": "string"},
                "ticket_id": {"type": ["string", "integer"]},
            },
            "required": ["script_name"],
        },
    },
]


async def handle(name: str, arguments: dict[str, Any], settings: Settings) -> dict[str, Any]:
    try:
        if name == "sandbox_write":
            root = _sandbox_root(arguments, settings)
            filepath = required_str(arguments, "filepath")
            content = required_str(arguments, "content")
            if len(content.encode("utf-8")) > settings.sandbox_max_write_bytes:
                return failure("WRITE_TOO_LARGE", "content exceeds SANDBOX_WORKER_MAX_WRITE_BYTES")
            target = _resolve(root, filepath)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return success(output=f"已写入: {filepath}")

        if name == "sandbox_read":
            root = _sandbox_root(arguments, settings)
            filepath = required_str(arguments, "filepath")
            target = _resolve(root, filepath)
            if not target.exists():
                return failure("FILE_NOT_FOUND", f"文件不存在: {filepath}")
            content = target.read_text(encoding="utf-8")
            return success(output=_read_snippet(content, filepath, arguments))

        if name == "sandbox_list":
            root = _sandbox_root(arguments, settings)
            raw_path = optional_str(arguments, "path")
            target = _resolve(root, raw_path) if raw_path else root
            if not target.exists():
                return failure("PATH_NOT_FOUND", f"路径不存在: {raw_path or '.'}")
            max_depth = optional_int(arguments, "max_depth", 2, minimum=0, maximum=6)
            limit = optional_int(arguments, "limit", 200, minimum=1, maximum=1000)
            return success(output=_list_tree(root, target, max_depth=max_depth, limit=limit))

        if name == "sandbox_search":
            root = _sandbox_root(arguments, settings)
            pattern = required_str(arguments, "pattern")
            raw_path = optional_str(arguments, "path")
            glob = optional_str(arguments, "glob")
            target = _resolve(root, raw_path) if raw_path else root
            if not target.exists():
                return failure("PATH_NOT_FOUND", f"路径不存在: {raw_path or '.'}")
            output = await _search(target, pattern, glob)
            return success(output=output or f"未找到匹配「{pattern}」的内容")

        if name == "sandbox_exec":
            root = _sandbox_root(arguments, settings)
            command = required_str(arguments, "command")
            cwd = optional_str(arguments, "cwd")
            work_dir = _resolve(root, cwd) if cwd else root
            return await _exec_command(command, work_dir=work_dir, root=root, settings=settings)

        if name == "run_script":
            root = _sandbox_root(arguments, settings)
            script_name = required_str(arguments, "script_name")
            script_args = arguments.get("args") or []
            if not isinstance(script_args, list) or not all(isinstance(arg, str) for arg in script_args):
                return failure("BAD_ARGUMENT", "args must be a list of strings")
            return await _run_script(script_name, script_args, work_dir=root, settings=settings)

        return failure("UNKNOWN_TOOL", f"unknown tool: {name}")
    except ValueError as e:
        return failure("BAD_ARGUMENT", str(e))
    except UnicodeDecodeError:
        return failure("BINARY_FILE", "file is not valid UTF-8")
    except Exception as e:  # noqa: BLE001
        return failure("INTERNAL_ERROR", str(e))


def _sandbox_root(arguments: dict[str, Any], settings: Settings) -> Path:
    base = Path(settings.sandbox_base_dir).resolve()
    raw = optional_str(arguments, "sandbox_dir")
    if raw:
        root = Path(raw).resolve()
    else:
        ticket_id = str(arguments.get("ticket_id") or "").strip()
        if not ticket_id:
            raise ValueError("ticket_id or sandbox_dir is required")
        root = (base / ticket_id).resolve()
    if not _is_within(root, base):
        raise ValueError("sandbox_dir is outside SANDBOX_WORKER_BASE_DIR")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve(root: Path, raw_path: str) -> Path:
    if raw_path.startswith("/"):
        raise ValueError("path must be relative to sandbox root")
    target = (root / raw_path).resolve()
    if not _is_within(target, root):
        raise ValueError("path escapes sandbox root")
    return target


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _read_snippet(content: str, filepath: str, arguments: dict[str, Any]) -> str:
    lines = content.splitlines()
    max_start = max(len(lines), 1)
    start = optional_int(arguments, "start_line", 1, minimum=1, maximum=max_start)
    max_end = max(len(lines), start)
    end = optional_int(
        arguments,
        "end_line",
        start + READ_MAX_LINES - 1,
        minimum=start,
        maximum=max_end,
    )
    selected = lines[start - 1:end]
    numbered = [f"{idx}|{line}" for idx, line in enumerate(selected, start=start)]
    output = "\n".join(numbered)
    if len(output) > READ_MAX_CHARS:
        output = output[:READ_MAX_CHARS] + "\n\n... (片段过长，已截断)"
    header = f"文件: {filepath}\n行号: {start}-{end}/{len(lines)}\n"
    if end < len(lines):
        header += "提示: 文件未完整返回；如需更多内容，请继续读取相邻行号区间。\n"
    return f"{header}\n{output}"


def _list_tree(root: Path, target: Path, *, max_depth: int, limit: int) -> str:
    lines = [f"目录树: {target.relative_to(root)}"]
    count = 0

    def walk(path: Path, depth: int) -> None:
        nonlocal count
        if count >= limit or depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
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
    return "\n".join(lines)


async def _search(target: Path, pattern: str, glob: str) -> str:
    output = await _search_with_rg(target, pattern, glob or None)
    if output is None:
        output = _search_with_python(target, pattern, glob or None)
    if not output:
        return ""
    return _trim_search_output(output, pattern)


async def _search_with_rg(target: Path, pattern: str, glob: str | None) -> str | None:
    cmd = ["rg", "-n", "--hidden", "--glob", "!.git", pattern, str(target)]
    if glob:
        cmd[4:4] = ["--glob", glob]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    stdout, stderr = await proc.communicate()
    if proc.returncode == 1:
        return ""
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())
    return stdout.decode("utf-8", errors="replace")


def _search_with_python(target: Path, pattern: str, glob: str | None) -> str:
    regex = re.compile(pattern, re.IGNORECASE)
    paths = [target] if target.is_file() else [p for p in target.rglob("*") if p.is_file() and ".git" not in p.parts]
    results: list[str] = []
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
                if len(results) >= 400:
                    results.append("... 已截断，最多显示 400 条匹配")
                    return "\n".join(results)
    return "\n".join(results)


def _trim_search_output(output: str, pattern: str) -> str:
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


async def _exec_command(command: str, *, work_dir: Path, root: Path, settings: Settings) -> dict[str, Any]:
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return failure("BAD_COMMAND", f"invalid shell quoting: {e}")
    if not tokens:
        return failure("BAD_COMMAND", "command is empty")
    if tokens[0] in BLOCKED_ARGS:
        return failure("COMMAND_BLOCKED", f"command is blocked: {tokens[0]}")
    if tokens[0] not in ALLOWED_COMMANDS:
        return failure("COMMAND_NOT_ALLOWED", f"command is not allowed: {tokens[0]}")
    external = _first_external_path(tokens, work_dir=work_dir, root=root)
    if external:
        return failure("PATH_OUTSIDE_SANDBOX", f"command references path outside sandbox: {external}")
    proc = await asyncio.create_subprocess_exec(
        *tokens,
        cwd=str(work_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=settings.sandbox_timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        return failure("COMMAND_TIMEOUT", f"command timed out after {settings.sandbox_timeout_seconds}s")
    output = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    text = f"退出码: {proc.returncode}\n"
    if output:
        text += f"\n{output}"
    if err:
        text += f"\n--- stderr ---\n{err}"
    if proc.returncode != 0:
        return {"success": False, "code": "COMMAND_NONZERO", "error": f"退出码: {proc.returncode}", "output": text[:10_000]}
    return success(output=text[:10_000])


async def _run_script(
    script_name: str,
    args: list[str],
    *,
    work_dir: Path,
    settings: Settings,
) -> dict[str, Any]:
    if script_name not in ALLOWED_SCRIPTS:
        return failure("SCRIPT_NOT_ALLOWED", f"script is not allowlisted: {script_name}")
    script = (Path(settings.sandbox_scripts_dir).resolve() / script_name).resolve()
    if not _is_within(script, Path(settings.sandbox_scripts_dir).resolve()):
        return failure("PATH_OUTSIDE_SCRIPTS_DIR", "script path escapes scripts dir")
    if not script.exists():
        return failure("SCRIPT_NOT_FOUND", f"script file does not exist: {script}")
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(script),
        *args,
        cwd=str(work_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=max(120.0, settings.sandbox_timeout_seconds))
    except asyncio.TimeoutError:
        proc.kill()
        return failure("SCRIPT_TIMEOUT", "script execution timed out")
    output = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    text = f"退出码: {proc.returncode}\n\n"
    if output:
        text += f"--- stdout ---\n{output}\n"
    if err:
        text += f"--- stderr ---\n{err}\n"
    if proc.returncode != 0:
        return {"success": False, "code": "SCRIPT_NONZERO", "error": f"脚本退出码: {proc.returncode}", "output": text[:10_000]}
    return success(output=text[:10_000])


def _first_external_path(tokens: list[str], *, work_dir: Path, root: Path) -> str:
    for token in tokens[1:]:
        value = token.split("=", 1)[1] if token.startswith("--") and "=" in token else token
        if value.startswith("-"):
            continue
        if value.startswith("/") or value.startswith("..") or "../" in value:
            path = Path(value) if value.startswith("/") else work_dir / value
            if not _is_within(path, root):
                return value
    return ""
