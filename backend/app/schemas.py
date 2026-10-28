"""
Pydantic schemas: the request/response contracts of the REST API.

These are also the shapes Role 1 wraps as MCP tool input/output schemas,
so they are kept deliberately small and explicit.
"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    Bucket,
    LeadOrigin,
    LeadSource,
    Occupation,
    PipelineStage,
)

# Interaction vocabulary (plain strings in the DB, validated here)
InteractionStatus = Literal[
    "pending_approval", "approved", "rejected", "executed", "blocked", "skipped"
]
ConsentCheckResult = Literal["passed", "blocked", "error"]


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


class LeadCreate(BaseModel):
    """Raw, UN-standardized enquiry-form input from a prospective learner.
    Types are deliberately loose (source, occupation are free strings);
    standardize_lead_input() coerces them before anything reaches the DB.
    Unknown keys (e.g. a B2B `company` field from an old client) are ignored."""

    name: str
    email: str
    phone: Optional[str] = None
    source: Optional[str] = None  # "fb ad", "Referral", ...
    product_interest: Optional[str] = None  # course of interest, e.g. "Data Science"
    notes: Optional[str] = None
    consent: Optional[dict[str, bool]] = None  # {"marketing": true, ...}

    # Agent A scoring inputs; all optional
    lead_origin: Optional[str] = None  # "Landing Page", "API", "Lead Add Form", ...
    occupation: Optional[str] = None  # "Working Professional", "Student", ...
    total_visits: Optional[float] = Field(None, ge=0)
    time_on_website_sec: Optional[float] = Field(None, ge=0)
    pages_per_visit: Optional[float] = Field(None, ge=0)


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    phone: Optional[str]
    source: LeadSource
    product_interest: Optional[str]
    notes: Optional[str]
    lead_origin: LeadOrigin
    occupation: Occupation
    total_visits: Optional[int]
    time_on_website_sec: Optional[int]
    pages_per_visit: Optional[float]
    consent: list[dict[str, Any]]
    pipeline_stage: PipelineStage
    created_at: datetime
    last_activity_at: datetime
    lead_score: Optional[float]
    bucket: Bucket
    top_factors: Optional[list[str]]
    scored_at: Optional[datetime]
    model_version: Optional[str]


class LeadScoreUpdate(BaseModel):
    """Sent by Agent A (Role 3) via PATCH /leads/{id}/score."""

    score: float = Field(ge=0.0, le=1.0)
    bucket: Bucket
    top_factors: list[str] = Field(default_factory=list)
    model_version: str = "v1"


class LeadStageUpdate(BaseModel):
    """Sent by Agent B (Role 4) via PATCH /leads/{id}/stage."""

    pipeline_stage: PipelineStage


class LeadConsentUpdate(BaseModel):
    """PATCH /leads/{id}/consent. Only the purposes listed are changed;
    other purposes keep their existing record."""

    consent: dict[str, bool] = Field(min_length=1)


class ConsentCheckRequest(BaseModel):
    lead_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    actor: str = Field(default="agent_b", min_length=1)
    action_attempted: str = Field(default="consent_check", min_length=1)


class ConsentCheckOut(BaseModel):
    lead_id: str
    purpose: str
    allowed: bool
    result: Literal["allowed", "blocked"]
    reason: str
    consent_on_file: bool
    audit_log_id: str
    timestamp: datetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_id: str
    lead_id: str
    actor: str
    action_attempted: str
    purpose_required: str
    consent_on_file: bool
    result: str
    timestamp: datetime


class StaleLeadOut(BaseModel):
    lead_id: str
    last_activity_at: datetime
    stale: bool
    retention_days: int


# ---------------------------------------------------------------------------
# Interactions (agent actions: the `log_interaction` tool)
# ---------------------------------------------------------------------------


class InteractionCreate(BaseModel):
    actor: str = "agent_b"
    action_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    status: InteractionStatus = "pending_approval"
    consent_check: Optional[ConsentCheckResult] = None


class InteractionStatusUpdate(BaseModel):
    status: InteractionStatus


class InteractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lead_id: str
    actor: str
    action_type: str
    payload: dict[str, Any]
    status: str
    consent_check: Optional[str]
    created_at: datetime
    updated_at: datetime
