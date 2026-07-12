"""知识源选择器：根据工单内容给出默认检索建议。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRecommendation:
    """单个知识源建议。"""

    source: str
    score: int
    reason: str


def suggest_sources(
    *,
    subject: str,
    description: str,
    product: str = "",
    tags: list[str] | None = None,
    custom_fields: dict[str, str] | None = None,
) -> list[SourceRecommendation]:
    """根据工单内容给出默认知识源建议。

    这是代码层的默认路由，只负责给出“先查哪里”的建议，
    最终是否采纳、是否交叉验证，仍由 Agent 在 prompt 中自主判断。
    """

    merged_text = " ".join([
        subject or "",
        description or "",
        product or "",
        " ".join(tags or []),
        " ".join(f"{k}:{v}" for k, v in (custom_fields or {}).items()),
    ]).lower()

    is_enterprise = _contains_any(
        merged_text,
        ["enterprise", "企业版", "private cloud", "license", "workspace id"],
    )
    is_community = _contains_any(
        merged_text,
        ["community", "开源版", "self-hosted"],
    ) and not is_enterprise

    scores: dict[str, int] = {
        "zendesk_kb": 68,
        "local_knowledge": 52,
    }
    reasons: dict[str, str] = {
        "zendesk_kb": "优先查历史支持知识和标准处理口径",
        "local_knowledge": "补充项目内沉淀的历史经验与设计说明",
    }

    if is_enterprise:
        _bump(scores, reasons, "official_docs_enterprise", 82, "企业版问题优先查企业官方文档")
    elif is_community:
        _bump(scores, reasons, "official_docs_community", 82, "社区版问题优先查社区官方文档")
    else:
        _bump(scores, reasons, "official_docs_community", 70, "默认先查社区官方文档")
        _bump(scores, reasons, "official_docs_enterprise", 64, "若工单更像企业版能力，再交叉查企业文档")

    if _contains_any(
        merged_text,
        [
            "helm", "chart", "upgrade", "deploy", "deployment", "docker compose",
            "kubernetes", "k8s", "ingress", "environment variable", "环境变量",
            "安装", "部署", "升级", "镜像", "qdrant", "redis",
        ],
    ):
        _bump(scores, reasons, "official_docs_enterprise", 78, "部署类问题通常需要对照部署文档")
        _bump(scores, reasons, "official_docs_community", 74, "社区部署说明可作为交叉参考")

    if _contains_any(
        merged_text,
        ["sso", "saml", "oidc", "oauth", "okta", "azure", "github 登录", "认证", "登录失败"],
    ):
        _bump(scores, reasons, "official_docs_enterprise", 88, "认证/SSO 问题优先查企业版认证文档")
        _bump(scores, reasons, "source_code", 62, "若配置无误仍异常，需要再下钻源码验证实现逻辑")

    if _contains_any(
        merged_text,
        ["/v1/", "/api/", "api", "endpoint", "401", "403", "404", "chat-messages", "workflow"],
    ):
        _bump(scores, reasons, "official_docs_community", 86, "API / Endpoint 问题优先查官方 API 文档")

    if _contains_any(
        merged_text,
        [
            "error", "exception", "traceback", "bug", "regression", "报错",
            "堆栈", "代码", "前端", "后端", "函数", "源码", "null",
        ],
    ):
        _bump(scores, reasons, "source_code", 84, "疑似实现缺陷或回归，必须结合源码确认")

    if _contains_any(
        merged_text,
        ["screenshot", "image", "图片", "截图", "附件", "attachment"],
    ):
        _bump(scores, reasons, "attachments", 72, "有截图/附件时应把附件内容纳入证据")

    ordered = sorted(
        (
            SourceRecommendation(source=source, score=score, reason=reasons[source])
            for source, score in scores.items()
        ),
        key=lambda item: (-item.score, item.source),
    )
    return ordered


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _bump(
    scores: dict[str, int],
    reasons: dict[str, str],
    source: str,
    score: int,
    reason: str,
) -> None:
    current = scores.get(source, 0)
    if score > current:
        scores[source] = score
        reasons[source] = reason
