"""Helm Watchdog REST API 工具组

基于 https://dify-helm-watchdog.vercel.app/openapi.json 封装，
提供 Dify Helm chart 版本查询、镜像列表、验证报告等能力。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="helm_watchdog")


def _get_client() -> tuple[str, int]:
    """返回 (base_url, timeout)"""
    cfg = get_config().helm_watchdog
    return cfg.url.rstrip("/"), cfg.timeout


async def _api_get(path: str, params: dict[str, Any] | None = None) -> ToolResult:
    """统一的 GET 请求封装"""
    base_url, timeout = _get_client()
    url = f"{base_url}{path}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "yaml" in content_type or "text/plain" in content_type:
                return ToolResult(output=resp.text[:10000])

            data = resp.json()
            return ToolResult(output=json.dumps(data, indent=2, ensure_ascii=False)[:10000])

    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response else ""
        return ToolResult(success=False, error=f"HTTP {e.response.status_code}: {body}")
    except httpx.ConnectError:
        return ToolResult(success=False, error=f"无法连接 Helm Watchdog: {base_url}")
    except httpx.TimeoutException:
        return ToolResult(success=False, error=f"请求超时 ({timeout}s): {url}")
    except Exception as e:
        return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


# ── 工具定义 ────────────────────────────────────────────────


class HelmListVersions(Tool):
    name = "helm_list_versions"
    description = (
        "列出所有可用的 Dify Helm chart 版本。"
        "返回版本号、appVersion、创建时间。"
        "可选包含镜像验证统计。用于确认客户版本是否存在。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "includeValidation": {
                "type": "boolean",
                "description": "是否包含每个版本的镜像验证统计（total/allFound/partial/missing）",
            },
        },
    }

    group = "helm"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        params: dict[str, Any] = {}
        if args.get("includeValidation"):
            params["includeValidation"] = "true"
        return await _api_get("/api/v1/versions", params or None)


class HelmGetLatestVersion(Tool):
    name = "helm_get_latest_version"
    description = "获取最新的 Dify Helm chart 版本信息，包括版本号、appVersion、资源链接。"
    parameters = {
        "type": "object",
        "properties": {},
    }

    group = "helm"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        return await _api_get("/api/v1/versions/latest")


class HelmGetVersionDetails(Tool):
    name = "helm_get_version_details"
    description = (
        "获取指定版本的详细元数据：chart URL、digest、values.yaml 位置等。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "version": {
                "type": "string",
                "description": "chart 版本号（如 1.0.0）",
            },
        },
        "required": ["version"],
    }

    group = "helm"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        version = args.get("version", "")
        if not version:
            return ToolResult(success=False, error="缺少 version 参数")
        return await _api_get(f"/api/v1/versions/{version}")


class HelmListImages(Tool):
    name = "helm_list_images"
    description = (
        "列出指定版本 Helm chart 声明的所有容器镜像。"
        "可选包含每个镜像的验证状态。用于排查镜像拉取问题。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "version": {
                "type": "string",
                "description": "chart 版本号（如 1.0.0）",
            },
            "includeValidation": {
                "type": "boolean",
                "description": "是否包含验证状态",
            },
        },
        "required": ["version"],
    }

    group = "helm"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        version = args.get("version", "")
        if not version:
            return ToolResult(success=False, error="缺少 version 参数")
        params: dict[str, Any] = {}
        if args.get("includeValidation"):
            params["includeValidation"] = "true"
        return await _api_get(f"/api/v1/versions/{version}/images", params or None)


class HelmValidateImages(Tool):
    name = "helm_validate_images"
    description = (
        "获取指定版本的镜像验证报告：哪些镜像存在于目标 registry，"
        "amd64/arm64 架构支持情况。可仅返回缺失镜像。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "version": {
                "type": "string",
                "description": "chart 版本号（如 1.0.0）",
            },
            "onlyMissing": {
                "type": "boolean",
                "description": "仅返回状态为 MISSING 的镜像",
            },
        },
        "required": ["version"],
    }

    group = "helm"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        version = args.get("version", "")
        if not version:
            return ToolResult(success=False, error="缺少 version 参数")
        params: dict[str, Any] = {}
        if args.get("onlyMissing"):
            params["isMissing"] = "true"
        return await _api_get(f"/api/v1/versions/{version}/validation", params or None)


class HelmGetValues(Tool):
    name = "helm_get_values"
    description = (
        "下载指定版本的 values.yaml 内容。"
        "用于查看默认配置项、镜像地址、资源限制等。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "version": {
                "type": "string",
                "description": "chart 版本号（如 1.0.0）",
            },
        },
        "required": ["version"],
    }

    group = "helm"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        version = args.get("version", "")
        if not version:
            return ToolResult(success=False, error="缺少 version 参数")
        return await _api_get(f"/api/v1/versions/{version}/values")
