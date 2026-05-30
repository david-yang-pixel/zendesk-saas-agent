"""线上部署版本解析：SigNoz 主源（经 sz CLI）+ saas-deploy 兜底。

- resolve_deployed_version()：返回 cloud.dify.ai 生产 dify-api 当前运行版本。
- SaasDeployedVersion：agent 深挖工具。
版本信息是锦上添花，任何失败都返回 None / 明确 error，绝不中断工单处理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_BASE_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


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
