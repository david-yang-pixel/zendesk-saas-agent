"""Compatibility exports for the split casework domain model modules."""

from zd_agent.casework.domain.base import FrozenModel, TargetReference
from zd_agent.casework.domain.context import (
    AttachmentRef,
    BillingBinding,
    BillingLookup,
    BillingSnapshot,
    CaseContext,
    FormIdentity,
    IdentityResolution,
    TicketSnapshot,
    VisualObservation,
    WorkspaceCandidate,
    WorkspaceMembershipLookup,
)
from zd_agent.casework.domain.decision import (
    DispositionDecision,
    ReplyCandidate,
    ReplyDraft,
    ReplyQualityResult,
    RetryDirective,
)
from zd_agent.casework.domain.evidence import (
    ActionProposal,
    AdapterFailure,
    AssessmentBuild,
    AssessmentProposal,
    CaseAssessment,
    Claim,
    ClaimAdmission,
    EvidenceItem,
    HumanActionRequest,
    InvestigationResult,
)
from zd_agent.casework.domain.effects import CaseworkEffectReceipt, CaseworkExecution
from zd_agent.casework.domain.run import CaseworkRun

__all__ = [
    "ActionProposal",
    "AdapterFailure",
    "AssessmentBuild",
    "AssessmentProposal",
    "AttachmentRef",
    "BillingBinding",
    "BillingLookup",
    "BillingSnapshot",
    "CaseAssessment",
    "CaseContext",
    "CaseworkRun",
    "CaseworkEffectReceipt",
    "CaseworkExecution",
    "Claim",
    "ClaimAdmission",
    "DispositionDecision",
    "EvidenceItem",
    "FormIdentity",
    "FrozenModel",
    "HumanActionRequest",
    "IdentityResolution",
    "InvestigationResult",
    "ReplyCandidate",
    "ReplyDraft",
    "ReplyQualityResult",
    "RetryDirective",
    "TargetReference",
    "TicketSnapshot",
    "VisualObservation",
    "WorkspaceCandidate",
    "WorkspaceMembershipLookup",
]
