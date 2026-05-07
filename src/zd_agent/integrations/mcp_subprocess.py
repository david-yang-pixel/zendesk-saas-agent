"""轻量 MCP stdio JSON-RPC client，把外部 MCP server 当子进程跑。

只支持我们用得上的：``initialize`` → ``notifications/initialized`` →
``tools/list`` / ``tools/call``。不实现 resources / prompts / sampling。

进程 launches lazily on first request，长驻常用于 zd-agent 整个 session。
所有 stdio I/O 单线程顺序，不并发（sufficient for our N=20 tool fan-out
since LLM only fires one tool at a time inside subagent_parallel slot）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    pass


class MCPSubprocessClient:
    """Long-running MCP server subprocess controlled via stdio JSON-RPC."""

    def __init__(
        self,
        *,
        binary: str,
        env: dict[str, str] | None = None,
        protocol_version: str = "2024-11-05",
        client_name: str = "zd-agent",
        client_version: str = "0.1",
    ) -> None:
        self.binary = binary
        self.env = env or {}
        self.protocol_version = protocol_version
        self.client_name = client_name
        self.client_version = client_version
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return

        import os
        merged_env = {**os.environ, **self.env}
        self._proc = await asyncio.create_subprocess_exec(
            self.binary,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        # Bump StreamReader limits to 16 MiB — MCP tools/list returns ~80KB
        # for 20 tools and individual tool/call results can be 1+ MB (logs).
        # asyncio default is 64KB and `Separator is found, but chunk is longer
        # than limit` errors out reading anything > 64KB.
        for stream in (self._proc.stdout, self._proc.stderr):
            if stream is not None and hasattr(stream, "_limit"):
                stream._limit = 16 * 1024 * 1024  # type: ignore[attr-defined]

        # initialize handshake
        init_resp = await self._raw_request({
            "method": "initialize",
            "params": {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": self.client_name,
                    "version": self.client_version,
                },
            },
        })
        if "error" in init_resp:
            raise MCPClientError(f"initialize failed: {init_resp['error']}")
        # send initialized notification (no response expected)
        await self._raw_send({"method": "notifications/initialized"})
        self._initialized = True

    async def _raw_send(self, msg: dict) -> None:
        """Send a JSON-RPC message; if it has 'method' but no 'id', it's a notification."""
        await self._ensure_proc_alive()
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n"
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def _raw_request(self, msg: dict, *, timeout: float = 60.0) -> dict:
        """Send a JSON-RPC request and read the matching response (single-flight via lock)."""
        async with self._lock:
            self._next_id += 1
            req_id = self._next_id
            full_msg = {"jsonrpc": "2.0", "id": req_id, **msg}
            await self._raw_send(full_msg)

            # Read responses until we get one with matching id (skipping notifications/log)
            assert self._proc and self._proc.stdout
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise MCPClientError(f"timeout waiting for response id={req_id}")
                try:
                    line = await asyncio.wait_for(
                        self._proc.stdout.readline(), timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    raise MCPClientError(f"timeout reading response id={req_id}")
                if not line:
                    raise MCPClientError("MCP server closed stdout unexpectedly")
                try:
                    obj = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    # Skip non-JSON output (debug logs etc.)
                    continue
                if isinstance(obj, dict) and obj.get("id") == req_id:
                    return obj
                # else it's a different id / notification — keep reading

    async def _ensure_proc_alive(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            raise MCPClientError("MCP subprocess not running")

    async def list_tools(self) -> list[dict]:
        await self._ensure_started()
        resp = await self._raw_request({"method": "tools/list", "params": {}})
        if "error" in resp:
            raise MCPClientError(f"tools/list error: {resp['error']}")
        return resp.get("result", {}).get("tools", [])

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> dict:
        """Call MCP tool; return the raw result dict (with content/isError/etc.)."""
        await self._ensure_started()
        resp = await self._raw_request(
            {
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            timeout=timeout,
        )
        if "error" in resp:
            raise MCPClientError(f"tool/call {name} error: {resp['error']}")
        return resp.get("result", {})

    async def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
            if self._proc.returncode is None:
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
        finally:
            self._proc = None
            self._initialized = False
