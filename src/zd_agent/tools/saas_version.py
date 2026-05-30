"""线上部署版本解析：SigNoz 主源（经 sz CLI）+ saas-deploy 兜底。

- resolve_deployed_version()：返回 cloud.dify.ai 生产 dify-api 当前运行版本。
- SaasDeployedVersion：agent 深挖工具。
版本信息是锦上添花，任何失败都返回 None / 明确 error，绝不中断工单处理。
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from zd_agent.config import get_config
from zd_agent.integrations.sz_cli import run_sz
from zd_agent.log import get_logger
from zd_agent.repos import fetch_repo, resolve_repo

logger = get_logger(module="tools.saas_version")

_BASE_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")

_TARGET_DEPLOYMENT = "dify-api-oltp"
_KUSTOMIZATION_PATH = "environments/production/dify/api-oltp/kustomization.yaml"
_CACHE_TTL_SECONDS = 300.0
_VERSION_SQL = (
    "SELECT DISTINCT "
    "resources_string['container.image.tag']  AS image_tag, "
    "resources_string['service.version']      AS service_version "
    "FROM signoz_logs.distributed_logs_v2 "
    "WHERE timestamp >= {start_ns} "
    "AND resources_string['k8s.deployment.name'] = '" + _TARGET_DEPLOYMENT + "' "
    "AND resources_string['deployment.environment'] = 'production' "
    "LIMIT 5"
)


@dataclass(frozen=True)
class DeployedVersion:
    image_tag: str        # SigNoz container.image.tag，如 hotfix-1.14.2-fix.4
    git_ref: str          # 转换+验证后可 git_show 的 ref，如 hotfix/1.14.2-fix.4
    base_version: str     # 1.14.2
    ref_verified: bool    # git_ref 是否真的在 dify 仓存在
    source: str           # "signoz" | "saas-deploy-fallback"


def image_tag_to_git_ref(tag: str) -> str:
    """镜像 tag（连字符）→ dify 仓 git ref（斜杠）。

    hotfix-1.14.2-fix.4 → hotfix/1.14.2-fix.4
    纯版本 tag（1.14.2 / v1.14.2）原样返回。
    """
    if not tag:
        return ""
    if tag.startswith("hotfix-"):
        return "hotfix/" + tag[len("hotfix-"):]
    return tag


def extract_base_version(tag: str) -> str:
    """从 tag 抽取 x.y.z 基线版本；抽不到返回空串。"""
    m = _BASE_VERSION_RE.search(tag or "")
    return m.group(1) if m else ""


# 进程内缓存
_cache: tuple[float, DeployedVersion | None] | None = None


def _reset_cache_for_test() -> None:
    global _cache
    _cache = None


async def _verify_ref(ref: str) -> bool:
    """git rev-parse --verify <ref>；失败先 auto-fetch dify 仓再验一次。"""
    if not ref:
        return False
    repo_dir = resolve_repo("dify")
    if repo_dir is None:
        return False

    async def _rev_parse() -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_dir), "rev-parse", "--verify", "-q", f"{ref}^{{commit}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0

    if await _rev_parse():
        return True
    # auto-fetch dify 仓后重试
    try:
        cfg = get_config()
        for entry in cfg.repos.repositories:
            if entry.name == "dify":
                await asyncio.to_thread(fetch_repo, entry)
                break
    except Exception as e:
        logger.warning("verify_ref_fetch_failed", error=str(e))
    return await _rev_parse()


async def _query_signoz_image_tag() -> str | None:
    """经 sz 查 dify-api-oltp 的 container.image.tag。多 tag 时取第一个。"""
    rows = await run_sz("query", _VERSION_SQL, "--hours", "3")
    if not rows or not isinstance(rows, list):
        return None
    for row in rows:
        tag = (row or {}).get("image_tag")
        if tag:
            return str(tag)
    return None


async def _read_saas_deploy_tag() -> str | None:
    """兜底：从 saas-deploy kustomization 读 newTag（先 fetch 求新鲜）。"""
    repo_dir = resolve_repo("saas-deploy")
    if repo_dir is None:
        return None
    try:
        cfg = get_config()
        for entry in cfg.repos.repositories:
            if entry.name == "saas-deploy":
                await asyncio.to_thread(fetch_repo, entry)
                break
    except Exception as e:
        logger.warning("saas_deploy_fetch_failed", error=str(e))

    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo_dir), "show", f"main:{_KUSTOMIZATION_PATH}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out_b, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    for line in out_b.decode("utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("newTag:"):
            return s.split(":", 1)[1].strip()
    return None


async def resolve_deployed_version(*, use_cache: bool = True) -> DeployedVersion | None:
    """解析 cloud.dify.ai 生产 dify-api 当前运行版本。读不到 → None，绝不抛。"""
    global _cache
    if use_cache and _cache is not None:
        ts, cached = _cache
        if (time.monotonic() - ts) < _CACHE_TTL_SECONDS:
            return cached

    result: DeployedVersion | None = None
    try:
        tag = await _query_signoz_image_tag()
        source = "signoz"
        if not tag:
            tag = await _read_saas_deploy_tag()
            source = "saas-deploy-fallback"
        if tag:
            git_ref = image_tag_to_git_ref(tag)
            verified = await _verify_ref(git_ref)
            result = DeployedVersion(
                image_tag=tag,
                git_ref=git_ref if verified else tag,
                base_version=extract_base_version(tag),
                ref_verified=verified,
                source=source,
            )
    except Exception as e:
        logger.warning("resolve_deployed_version_failed", error=str(e))
        result = None

    _cache = (time.monotonic(), result)
    return result
