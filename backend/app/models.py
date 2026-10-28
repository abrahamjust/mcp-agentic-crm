"""
SQLAlchemy ORM models.

leads        -> one row per lead; every field is stored STANDARDIZED.
                A lead is an individual learner enquiring about a course
                (the client is an education provider), not a company.
interactions -> one row per agent action (what Agent B logs through the
                `log_interaction` MCP tool). Also what lets an agent
                tell "this Hot lead was already handled" after a restart.

Timestamps: SQLite has no timezone support, so UTCDateTime stores naive
UTC and hands back timezone-aware UTC. API output is therefore always
unambiguous ("...Z"), which matters for the frontend's date parsing.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import validates
from sqlalchemy.types import TypeDecorator

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Store naive UTC, return timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class LeadSource(str, enum.Enum):
    WEBSITE = "Website"
    REFERRAL = "Referral"
    SOCIAL = "Social"
    COLD_OUTREACH = "Cold Outreach"
    EVENT = "Event"
    UNKNOWN = "Unknown"


class LeadOrigin(str, enum.Enum):
    """How the lead entered the CRM (Agent A model input)."""

    LANDING_PAGE = "Landing Page Submission"
    API = "API"
    LEAD_ADD_FORM = "Lead Add Form"
    LEAD_IMPORT = "Lead Import"
    UNKNOWN = "Unknown"


class Occupation(str, enum.Enum):
    """The lead's current occupation (Agent A model input)."""

    WORKING_PROFESSIONAL = "Working Professional"
    BUSINESS_OWNER = "Business Owner"
    STUDENT = "Student"
    UNEMPLOYED = "Unemployed"
    HOMEMAKER = "Homemaker"
    OTHER = "Other"
    UNKNOWN = "Unknown"


class Bucket(str, enum.Enum):
    HOT = "Hot"
    WARM = "Warm"
    COLD = "Cold"
    UNSCORED = "Unscored"


class PipelineStage(str, enum.Enum):
    NEW = "New"
    CONTACTED = "Contacted"
    QUALIFIED = "Qualified"
    WON = "Won"
    LOST = "Lost"


class Lead(Base):
    __tablename__ = "leads"

    id = Column(String, primary_key=True, default=_uuid)

    # --- form fields (standardized before storage: app/standardize.py) ---
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)
    phone = Column(String, nullable=True)
    source = Column(Enum(LeadSource), default=LeadSource.UNKNOWN, nullable=False)
    product_interest = Column(String, nullable=True)  # the course / programme asked about
    notes = Column(Text, nullable=True)

    # --- scoring inputs for Agent A (all optional; the model handles blanks) ---
    lead_origin = Column(Enum(LeadOrigin), default=LeadOrigin.UNKNOWN, nullable=False)
    occupation = Column(Enum(Occupation), default=Occupation.UNKNOWN, nullable=False)
    # web-analytics engagement, normally filled by site tracking
    total_visits = Column(Integer, nullable=True)
    time_on_website_sec = Column(Integer, nullable=True)
    pages_per_visit = Column(Float, nullable=True)

    # list of {purpose, granted, timestamp}: one record per purpose
    consent = Column(JSON, default=list, nullable=False)

    # --- pipeline / system fields ---
    pipeline_stage = Column(Enum(PipelineStage), default=PipelineStage.NEW, nullable=False)
    created_at = Column(UTCDateTime, default=_now, nullable=False)
    last_activity_at = Column(UTCDateTime, default=_now, nullable=False)

    # --- Agent A output ---
    lead_score = Column(Float, nullable=True)  # 0.0-1.0
    bucket = Column(Enum(Bucket), default=Bucket.UNSCORED, nullable=False)
    top_factors = Column(JSON, default=list, nullable=True)
    scored_at = Column(UTCDateTime, nullable=True)
    model_version = Column(String, nullable=True)

    @validates("email")
    def _validate_email_present(self, key, value):
        if not value or "@" not in value:
            raise ValueError(f"Invalid email stored on Lead: {value!r}")
        return value


class Interaction(Base):
    """One logged agent action on a lead (the `log_interaction` tool)."""

    __tablename__ = "interactions"

    id = Column(String, primary_key=True, default=_uuid)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False, index=True)
    actor = Column(String, nullable=False, default="agent_b")
    # e.g. drafted_followup_email, moved_pipeline_stage, no_action_consent_blocked
    action_type = Column(String, nullable=False)
    payload = Column(JSON, default=dict, nullable=False)  # e.g. {subject, body}
    # pending_approval | approved | rejected | executed | blocked | skipped
    status = Column(String, nullable=False, default="pending_approval")
    # passed | blocked | error  (result of the consent check that preceded it)
    consent_check = Column(String, nullable=True)
    created_at = Column(UTCDateTime, default=_now, nullable=False)
    updated_at = Column(UTCDateTime, default=_now, onupdate=_now, nullable=False)


class AuditLog(Base):
    """Immutable record of every consent check requested by an agent."""

    __tablename__ = "audit_logs"

    log_id = Column(String, primary_key=True, default=_uuid)
    lead_id = Column(String, nullable=False, index=True)
    actor = Column(String, nullable=False)
    action_attempted = Column(String, nullable=False)
    purpose_required = Column(String, nullable=False, index=True)
    consent_on_file = Column(Boolean, nullable=False)
    result = Column(String, nullable=False)
    timestamp = Column(UTCDateTime, default=_now, nullable=False, index=True)
