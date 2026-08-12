from __future__ import annotations

import re

from zd_agent.casework.domain.enums import (
    AnswerStatus,
    ClaimRole,
    InvestigationScenario,
)
from zd_agent.casework.domain.models import Claim


_ASYNC_OBJECT = re.compile(
    r"(?:document|file|dataset|knowledge\s*base|documento|archivo|base\s+de\s+"
    r"conocimiento|文档|文件|知识库|資料|ドキュメント)",
    re.IGNORECASE,
)
_ASYNC_FAILURE = re.compile(
    r"(?:stuck|paused?|waiting|queued?|index(?:ing)?|ingest(?:ion)?|processing|"
    r"atascad[oa]s?|pausad[oa]s?|espera|proces(?:ando|amiento)|indexaci[oó]n|"
    r"卡住|卡死|暂停|等待|排队|索引|处理中)",
    re.IGNORECASE,
)
_BILLING = re.compile(
    r"(?:billing|subscription|plan|quota|credit|refund|invoice|payment|"
    r"订阅|套餐|额度|退款|发票|付款|suscripci[oó]n|factura|reembolso)",
    re.IGNORECASE,
)
_RETRIEVAL = re.compile(
    r"(?:retrieval|embedding|vector|knowledge\s+chat|检索|召回|嵌入|向量)",
    re.IGNORECASE,
)
_PROVIDER = re.compile(
    r"(?:provider|model|api\s*key|credential|llm|模型供应商|密钥)",
    re.IGNORECASE,
)
_RUNTIME_FAILURE = re.compile(
    r"(?:fail(?:ed|ing|ure)?|error|stuck|timeout|not\s+work|empty|no\s+response|"
    r"失败|报错|错误|卡住|超时|无响应|空回复|falla|error|atascad)",
    re.IGNORECASE,
)


def classify_investigation_scenario(question: str) -> InvestigationScenario:
    normalized = " ".join(question.split())
    if _ASYNC_OBJECT.search(normalized) and _ASYNC_FAILURE.search(normalized):
        return InvestigationScenario.ASYNC_PROCESSING
    if _BILLING.search(normalized):
        return InvestigationScenario.BILLING_ENTITLEMENT
    if _RETRIEVAL.search(normalized) and _RUNTIME_FAILURE.search(normalized):
        return InvestigationScenario.RETRIEVAL_RUNTIME
    if _PROVIDER.search(normalized) and _RUNTIME_FAILURE.search(normalized):
        return InvestigationScenario.PROVIDER_RUNTIME
    if _RUNTIME_FAILURE.search(normalized):
        return InvestigationScenario.WORKFLOW_RUNTIME
    return InvestigationScenario.GENERAL


def required_completion_roles(question: str) -> frozenset[ClaimRole]:
    scenario = classify_investigation_scenario(question)
    runtime_scenarios = {
        InvestigationScenario.ASYNC_PROCESSING,
        InvestigationScenario.RETRIEVAL_RUNTIME,
        InvestigationScenario.PROVIDER_RUNTIME,
        InvestigationScenario.WORKFLOW_RUNTIME,
    }
    if scenario in runtime_scenarios:
        return frozenset({ClaimRole.CURRENT_STATE, ClaimRole.FAILING_BOUNDARY})
    return frozenset()


def missing_completion_roles(
    question: str,
    claims: tuple[Claim, ...],
) -> tuple[ClaimRole, ...]:
    present = {claim.role for claim in claims if claim.material}
    return tuple(sorted(required_completion_roles(question) - present, key=str))


def enforce_investigation_completion(
    *,
    question: str,
    answer_status: AnswerStatus,
    admitted_claims: tuple[Claim, ...],
) -> AnswerStatus:
    if answer_status not in {AnswerStatus.SUPPORTED, AnswerStatus.BOUNDED}:
        return answer_status
    if missing_completion_roles(question, admitted_claims):
        return AnswerStatus.INSUFFICIENT
    return answer_status
