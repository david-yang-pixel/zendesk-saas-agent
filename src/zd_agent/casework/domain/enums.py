from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    CANARY = "canary"
    CASEWORK = "casework"


class Disposition(str, Enum):
    RESOLVED = "resolved"
    AWAITING_CUSTOMER = "awaiting_customer"
    RETRY_PENDING = "retry_pending"
    HUMAN_ACTION_REQUIRED = "human_action_required"
    ENGINEERING_ESCALATION = "engineering_escalation"
    SRE_ESCALATION = "sre_escalation"
    SECURITY_REVIEW = "security_review"


class ReplyKind(str, Enum):
    ANSWER = "answer"
    ASK = "ask"
    PROGRESS = "progress"
    CLOSE = "close"
    NONE = "none"


class OwnershipAction(str, Enum):
    NONE = "none"
    SUPPORT = "support"
    BILLING = "billing"
    ENGINEERING = "engineering"
    SRE = "sre"
    SECURITY = "security"


class ActionRequirement(str, Enum):
    CUSTOMER_REQUESTED_HUMAN = "customer_requested_human"
    BUSINESS_APPROVAL = "business_approval"
    SENSITIVE_ACCOUNT_MUTATION = "sensitive_account_mutation"
    CONFIRMED_PLATFORM_DEFECT = "confirmed_platform_defect"
    CONFIRMED_SERVICE_INCIDENT = "confirmed_service_incident"
    SECURITY_OR_PRIVACY_REVIEW = "security_or_privacy_review"
    POLICY_COMMITMENT = "policy_commitment"


class ReasonCode(str, Enum):
    RESOLVED_WITH_EVIDENCE = "resolved_with_evidence"
    BOUNDED_AUTHORITATIVE_ANSWER = "bounded_authoritative_answer"
    CUSTOMER_INPUT_REQUIRED = "customer_input_required"
    INTERNAL_RETRY_PENDING = "internal_retry_pending"
    CUSTOMER_REQUESTED_HUMAN = "customer_requested_human"
    BUSINESS_APPROVAL_REQUIRED = "business_approval_required"
    SENSITIVE_ACCOUNT_MUTATION_REQUIRED = "sensitive_account_mutation_required"
    CONFIRMED_PLATFORM_DEFECT = "confirmed_platform_defect"
    CONFIRMED_SERVICE_INCIDENT = "confirmed_service_incident"
    SECURITY_OR_PRIVACY_REVIEW_REQUIRED = "security_or_privacy_review_required"
    AUTOMATION_RETRY_EXHAUSTED = "automation_retry_exhausted"
    POLICY_COMMITMENT_REQUIRED = "policy_commitment_required"


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NO_MEMBERSHIP = "no_membership"
    CONFLICT = "conflict"
    LOOKUP_PENDING = "lookup_pending"
    LOOKUP_FAILED = "lookup_failed"


class ResolutionMethod(str, Enum):
    NONE = "none"
    UNIQUE_EMAIL_MEMBERSHIP = "unique_email_membership"
    EXPLICIT_CANONICAL_ID = "explicit_canonical_id"
    UNIQUE_EXACT_CANDIDATE_NAME = "unique_exact_candidate_name"
    CUSTOMER_CONFIRMED = "customer_confirmed"


class LookupStatus(str, Enum):
    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"


class BillingBindingStatus(str, Enum):
    RESOLVED = "resolved"
    IDENTITY_UNRESOLVED = "identity_unresolved"
    LOOKUP_PENDING = "lookup_pending"
    LOOKUP_FAILED = "lookup_failed"
    TARGET_MISMATCH = "target_mismatch"


class EvidenceAuthority(str, Enum):
    CURRENT_AUTHORITATIVE = "current_authoritative"
    CURRENT_SUPPORTING = "current_supporting"
    CONTROL_OR_BASELINE = "control_or_baseline"
    HISTORICAL_GUIDANCE = "historical_guidance"


class EvidenceResultStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


class ClaimRole(str, Enum):
    PRODUCT_FACT = "product_fact"
    CURRENT_STATE = "current_state"
    FAILING_BOUNDARY = "failing_boundary"
    ROOT_CAUSE = "root_cause"
    IMPACT = "impact"
    RECOVERY = "recovery"
    LIMITATION = "limitation"


class ClaimStatus(str, Enum):
    CONFIRMED = "confirmed"
    EVIDENCE_SUPPORTED = "evidence_supported"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"


class ClaimRejectionCode(str, Enum):
    NONE = "none"
    NOT_PUBLIC = "not_public"
    UNSUPPORTED_STATUS = "unsupported_status"
    CONTRADICTED = "contradicted"
    MISSING_EVIDENCE = "missing_evidence"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    FAILED_EVIDENCE = "failed_evidence"
    HISTORICAL_EVIDENCE = "historical_evidence"
    STALE_EVIDENCE = "stale_evidence"
    TARGET_MISMATCH = "target_mismatch"
    ROLE_MISMATCH = "role_mismatch"
    ROOT_CAUSE_INCOMPLETE = "root_cause_incomplete"


class AnswerStatus(str, Enum):
    SUPPORTED = "supported"
    BOUNDED = "bounded"
    INSUFFICIENT = "insufficient"


class AdapterErrorCode(str, Enum):
    TIMEOUT = "timeout"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    RATE_LIMITED = "rate_limited"
    AUTH_EXPIRED = "auth_expired"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    PERMISSION_DENIED = "permission_denied"
    INVALID_REQUEST = "invalid_request"
    MALFORMED_RESPONSE = "malformed_response"
    AMBIGUOUS_WRITE = "ambiguous_write"
    PERMANENT_FAILURE = "permanent_failure"
