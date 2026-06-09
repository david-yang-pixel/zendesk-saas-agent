"""异步日志上报器：将 agent 运行事件推送到 zendesk-hc-client。

设计原则：
- 未配置 HC_CLIENT_URL 时静默跳过
- 所有上报 fire-and-forget，失败只打 warning 不影响 agent 主流程
- HTTP 请求 3s 超时，不做重试
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiohttp

from zd_agent.config import EnvSettings
from zd_agent.log import get_logger
from zd_agent.runtime.events import LoopFinished, LoopProgress, ToolExecuted

logger = get_logger(module="log_reporter")

_TIMEOUT = aiohttp.ClientTimeout(total=3)


class LogReporter:
    """将 agent 事件上报到 hc-client 的 HTTP 客户端"""

    def __init__(self) -> None:
        env = EnvSettings()
        self._base_url = env.hc_client_url.rstrip("/") if env.hc_client_url else ""
        self._api_key = env.hc_client_api_key
        self._enabled = bool(self._base_url and self._api_key)
        self._session: aiohttp.ClientSession | None = None
        self._pending_tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=_TIMEOUT,
                headers={
                    "X-Agent-Key": self._api_key,
                    "Content-Type": "application/json",
                    "User-Agent": "zd-agent/1.0",
                },
            )
        return self._session

    async def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any] | None:
        if not self._enabled:
            return None
        try:
            session = self._get_session()
            async with session.post(f"{self._base_url}{path}", json=data) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    logger.warning("log_report_error", path=path, status=resp.status, body=text[:200])
                    return None
                return await resp.json()
        except Exception as e:
            logger.warning("log_report_failed", path=path, error=str(e))
            return None

    async def _put(self, path: str, data: dict[str, Any]) -> dict[str, Any] | None:
        if not self._enabled:
            return None
        try:
            session = self._get_session()
            async with session.put(f"{self._base_url}{path}", json=data) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    logger.warning("log_report_error", path=path, status=resp.status, body=text[:200])
                    return None
                return await resp.json()
        except Exception as e:
            logger.warning("log_report_failed", path=path, error=str(e))
            return None

    async def _post_background(self, path: str, data: dict[str, Any]) -> None:
        try:
            await self._post(path, data)
        except Exception as e:
            logger.warning("log_report_background_failed", path=path, error=str(e))

    def _schedule_post(self, path: str, data: dict[str, Any]) -> None:
        if not self._enabled:
            return
        task = asyncio.create_task(self._post_background(path, data))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def flush(self) -> None:
        """等待已排队的后台上报任务完成。"""
        if self._pending_tasks:
            await asyncio.gather(*list(self._pending_tasks), return_exceptions=True)

    async def report_run_start(
        self,
        ticket_id: int,
        session_id: str,
        trigger_type: str,
        model: str,
    ) -> int | None:
        """上报 agent 开始处理工单，返回远程 run_id"""
        result = await self._post(
            "/api/agent/logs/runs",
            {
                "ticket_id": ticket_id,
                "source": "saas",
                "session_id": session_id,
                "trigger_type": trigger_type,
                "model": model,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if result and "id" in result:
            logger.debug("run_reported", run_id=result["id"], ticket_id=ticket_id)
            return result["id"]
        return None

    async def report_round(
        self,
        run_id: int,
        event: LoopProgress,
        model: str,
    ) -> int | None:
        """上报一轮 LLM 调用，返回远程 round_id"""
        result = await self._post(
            f"/api/agent/logs/runs/{run_id}/rounds",
            {
                "round": event.round,
                "model": model,
                "input_tokens": event.prompt_tokens,
                "output_tokens": event.completion_tokens,
                "duration_ms": int(event.llm_elapsed * 1000),
                "assistant_content": (event.assistant_content or "")[:10000] or None,
                "stop_reason": event.stop_reason or None,
                "has_tool_calls": event.tool_call_count > 0,
                "tool_call_count": event.tool_call_count,
            },
        )
        if result and "id" in result:
            return result["id"]
        return None

    async def report_tool_call(
        self,
        run_id: int,
        round_id: int,
        event: ToolExecuted,
    ) -> None:
        """上报一次工具调用"""
        self._schedule_post(
            f"/api/agent/logs/runs/{run_id}/rounds/{round_id}/tool-calls",
            {
                "tool_name": event.tool_name,
                "arguments": event.arguments or None,
                "result_preview": (event.output[:4000]) if event.output else None,
                "success": event.success,
                "error_message": event.error,
                "duration_ms": int(event.elapsed * 1000),
            },
        )

    async def report_run_finish(
        self,
        run_id: int,
        event: LoopFinished,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> None:
        """上报 agent 处理结束"""
        await self.flush()
        await self._put(
            f"/api/agent/logs/runs/{run_id}",
            {
                "status": "completed" if event.status == "completed" else (
                    "timeout" if event.status == "guardrail" and "超时" in event.reason else
                    "failed"
                ),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": int(event.elapsed_seconds * 1000),
                "total_rounds": event.total_rounds,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "error_message": event.reason if event.status != "completed" else None,
            },
        )

    async def close(self) -> None:
        """关闭 HTTP 连接"""
        await self.flush()
        if self._session and not self._session.closed:
            await self._session.close()
