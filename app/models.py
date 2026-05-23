from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TrustedSource(BaseModel):
    source_id: str
    payer: str
    plan: str | None = None
    procedure_keywords: list[str] = Field(default_factory=list)
    approved_url: str
    trusted: bool = True
    baseline_fixture: str
    latest_fixture: str


class PolicyDocument(BaseModel):
    source_id: str
    payer: str
    url: str
    content: str
    retrieved_at: datetime = Field(default_factory=utc_now)
    via: str = "nimble"


class PolicySnapshot(BaseModel):
    source_id: str
    payer: str
    content_hash: str
    normalized_content: str
    timestamp: datetime = Field(default_factory=utc_now)
    via: str = "nimble"


class PolicyDiff(BaseModel):
    source_id: str
    payer: str
    previous_hash: str
    current_hash: str
    changed_lines: list[str] = Field(default_factory=list)
    summary: str
    has_changes: bool


class MaterialityResult(BaseModel):
    source_id: str
    classification: Literal["material", "non_material", "ambiguous"]
    confidence: float
    explanation: str
    impacted_keywords: list[str] = Field(default_factory=list)


class ActivePolicyChange(BaseModel):
    watch_id: str
    source_id: str
    payer: str
    plan_type: str | None = None
    source_url: str | None = None
    procedure_keywords: list[str]
    diff: PolicyDiff
    materiality: MaterialityResult
    effective_date: str | None = None
    change_type: str = "policy_revision"
    documentation_required: list[str] = Field(default_factory=list)
    changed_section: str | None = None
    detected_at: datetime = Field(default_factory=utc_now)


class ClinicalTriggerEvent(BaseModel):
    trigger_id: str
    hook: Literal["order-select", "order-sign"]
    payer: str
    plan: str | None = None
    procedure: str
    procedure_codes: list[str] = Field(default_factory=list)
    provider_action: str
    patient_context: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=utc_now)


class CRDDecision(BaseModel):
    trigger_id: str
    route: Literal["crd_guidance_ready", "suppress", "log_only", "human_review"]
    aligned: bool
    matched_watch_id: str | None = None
    reason: str
    suggested_next_step: str | None = None


class DTRRequest(BaseModel):
    trigger_id: str
    watch_id: str
    payer: str
    procedure: str
    required_evidence: list[str]
    documentation_gap: str


class ReadinessChecklist(BaseModel):
    payer: str
    procedure: str
    items: list[str]
    source: str = "senso"
    grounded_note: str


class SensoPublishResult(BaseModel):
    success: bool
    publisher_id: str
    publisher_slug: str = "cited-md"
    question_id: str | None = None
    content_id: str | None = None
    publish_record_id: str | None = None
    url: str | None = None
    message: str
    raw_response: dict[str, Any] = Field(default_factory=dict)


class PaymentRequirementResult(BaseModel):
    required: bool
    status_code: int
    payment_token_hint: str
    message: str


class PaymentVerificationResult(BaseModel):
    verified: bool
    transaction_id: str | None = None
    message: str


class ReadinessCard(BaseModel):
    trigger_id: str
    watch_id: str
    payer: str
    procedure: str
    recommendation: str
    required_evidence: list[str]
    grounded_note: str
    payment_transaction_id: str


class AgentEvent(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class PolicyChangeRecord(BaseModel):
    change_id: str
    watch_id: str
    policy_id: str
    payer: str
    plan_type: str | None = None
    detected_at: datetime = Field(default_factory=utc_now)
    effective_date: str | None = None
    change_type: str
    materiality: str
    summary: str
    documentation_required: list[str] = Field(default_factory=list)
    source_url: str
    diff_text: str
    payload: dict[str, Any] = Field(default_factory=dict)
