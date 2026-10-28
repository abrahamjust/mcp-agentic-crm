"""
Role 2: Core CRM Backend.

Leads
  POST   /leads                       create (raw input -> standardized -> stored)
  GET    /leads                       list; filters: bucket, stage, unscored, actioned; paging
  GET    /leads/{id}                  one lead
  PATCH  /leads/{id}/score            Agent A writes {score, bucket, top_factors}
  PATCH  /leads/{id}/stage            Agent B moves the pipeline stage
  PATCH  /leads/{id}/consent          change consent for one or more purposes

Interactions (what Agent B did; backs the `log_interaction` MCP tool)
  POST   /leads/{id}/interactions     log an agent action
  GET    /leads/{id}/interactions     actions on one lead
  GET    /interactions                recent actions across leads (activity feed)
  PATCH  /interactions/{id}/status    approve / reject / mark executed

Real time
  WS     /ws                          lead_created, lead_scored, lead_stage_changed,
                                      lead_consent_updated, interaction_logged,
                                      interaction_updated

Not in this module: check_consent and the audit log (Role 5).
"""
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, add_missing_columns, drop_removed_columns, engine, get_db
from app.events import manager
from app.consent import check_consent
from app.models import AuditLog, Bucket, Interaction, Lead, PipelineStage
from app.schemas import (
    InteractionCreate,
    InteractionOut,
    InteractionStatus,
    InteractionStatusUpdate,
    AuditLogOut,
    ConsentCheckOut,
    ConsentCheckRequest,
    LeadConsentUpdate,
    LeadCreate,
    LeadOut,
    LeadScoreUpdate,
    LeadStageUpdate,
    StaleLeadOut,
)
from app.standardize import normalize_consent, standardize_lead_input

Base.metadata.create_all(bind=engine)
add_missing_columns()  # upgrade a crm.db created before newer columns existed
drop_removed_columns()  # ...and one that still has the old B2B company columns

app = FastAPI(title="Agentic CRM: Core Backend (Role 2)")

# The dashboard runs on its own dev-server port, so the browser treats its
# calls to this API as cross-origin. Open CORS is fine for a local demo;
# restrict allow_origins before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIR / "assets"),
    name="frontend-assets",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_lead(db: Session, lead_id: str) -> Lead:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _get_interaction(db: Session, interaction_id: str) -> Interaction:
    item = db.get(Interaction, interaction_id)
    if not item:
        raise HTTPException(status_code=404, detail="Interaction not found")
    return item


def _lead_json(lead: Lead) -> dict:
    # mode="json" -> ISO timestamps + plain enum values, same as REST output
    return LeadOut.model_validate(lead).model_dump(mode="json")


def _interaction_json(item: Interaction) -> dict:
    return InteractionOut.model_validate(item).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Frontend pages
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def dashboard_page():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/pipeline", include_in_schema=False)
def pipeline_page():
    return FileResponse(FRONTEND_DIR / "pipeline.html")


@app.get("/approvals", include_in_schema=False)
def approvals_page():
    return FileResponse(FRONTEND_DIR / "approvals.html")


@app.get("/activity", include_in_schema=False)
def activity_page():
    return FileResponse(FRONTEND_DIR / "activity.html")


@app.get("/leads/new", include_in_schema=False)
def lead_form_page():
    return FileResponse(FRONTEND_DIR / "new-lead.html")


# ---------------------------------------------------------------------------
# Leads: create / read
# ---------------------------------------------------------------------------


@app.post("/leads", response_model=LeadOut, status_code=201)
async def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    """Standardize, store, then broadcast so the dashboard shows the card
    immediately (before any scoring has happened)."""
    try:
        clean = standardize_lead_input(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    lead = Lead(**clean, bucket=Bucket.UNSCORED)
    db.add(lead)
    db.commit()
    db.refresh(lead)

    await manager.broadcast("lead_created", _lead_json(lead))
    return lead


@app.get("/leads", response_model=list[LeadOut])
def list_leads(
    bucket: Optional[Bucket] = None,
    stage: Optional[PipelineStage] = None,
    unscored: Optional[bool] = None,
    actioned: Optional[bool] = Query(
        None,
        description="true = leads with at least one logged interaction; "
        "false = leads with none (what Agent B polls after a restart)",
    ),
    limit: Optional[int] = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Filters map to the `get_leads(filter)` MCP tool:
    bucket=Hot (Agent B), unscored=true (Agent A), actioned=false (Agent B)."""
    query = db.query(Lead)
    if bucket is not None:
        query = query.filter(Lead.bucket == bucket)
    if stage is not None:
        query = query.filter(Lead.pipeline_stage == stage)
    if unscored:
        query = query.filter(Lead.bucket == Bucket.UNSCORED)
    if actioned is not None:
        acted_ids = select(Interaction.lead_id).distinct()
        if actioned:
            query = query.filter(Lead.id.in_(acted_ids))
        else:
            query = query.filter(Lead.id.not_in(acted_ids))

    query = query.order_by(Lead.created_at.desc()).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return query.all()


@app.get("/leads/stale", response_model=list[StaleLeadOut])
def list_stale_leads(
    days: Optional[int] = Query(None, ge=1, description="Inactivity threshold in days"),
    db: Session = Depends(get_db),
):
    """Flag leads inactive beyond the configured retention period."""
    retention_days = days if days is not None else int(
        os.environ.get("CONSENT_RETENTION_DAYS", "90")
    )
    cutoff = _now() - timedelta(days=retention_days)
    leads = (
        db.query(Lead)
        .filter(Lead.last_activity_at < cutoff)
        .order_by(Lead.last_activity_at.asc())
        .all()
    )
    return [
        StaleLeadOut(
            lead_id=lead.id,
            last_activity_at=lead.last_activity_at,
            stale=True,
            retention_days=retention_days,
        )
        for lead in leads
    ]


@app.get("/leads/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: str, db: Session = Depends(get_db)):
    return _get_lead(db, lead_id)


# ---------------------------------------------------------------------------
# Leads: write-back from the agents and consent changes
# ---------------------------------------------------------------------------


@app.patch("/leads/{lead_id}/score", response_model=LeadOut)
async def update_lead_score(
    lead_id: str, payload: LeadScoreUpdate, db: Session = Depends(get_db)
):
    """Agent A (Role 3) writes its prediction back (`update_lead_score`)."""
    lead = _get_lead(db, lead_id)
    lead.lead_score = payload.score
    lead.bucket = payload.bucket
    lead.top_factors = list(payload.top_factors)
    lead.model_version = payload.model_version
    lead.scored_at = _now()
    db.commit()
    db.refresh(lead)

    await manager.broadcast("lead_scored", _lead_json(lead))
    return lead


@app.patch("/leads/{lead_id}/stage", response_model=LeadOut)
async def update_lead_stage(
    lead_id: str, payload: LeadStageUpdate, db: Session = Depends(get_db)
):
    """Agent B (Role 4) advances the pipeline (`update_deal_stage`)."""
    lead = _get_lead(db, lead_id)
    lead.pipeline_stage = payload.pipeline_stage
    lead.last_activity_at = _now()
    db.commit()
    db.refresh(lead)

    await manager.broadcast("lead_stage_changed", _lead_json(lead))
    return lead


@app.patch("/leads/{lead_id}/consent", response_model=LeadOut)
async def update_lead_consent(
    lead_id: str, payload: LeadConsentUpdate, db: Session = Depends(get_db)
):
    """Change consent for the listed purposes only; other purposes keep their
    record. Each changed purpose gets a fresh timestamp. The consent check
    reads this live, so a withdrawal takes effect on the very next action."""
    lead = _get_lead(db, lead_id)

    by_purpose = {c["purpose"]: c for c in (lead.consent or [])}
    for record in normalize_consent(payload.consent):
        by_purpose[record["purpose"]] = record
    lead.consent = list(by_purpose.values())  # new list so the JSON change is detected
    lead.last_activity_at = _now()
    db.commit()
    db.refresh(lead)

    await manager.broadcast("lead_consent_updated", _lead_json(lead))
    return lead


# ---------------------------------------------------------------------------
# Consent and compliance (Role 5)
# ---------------------------------------------------------------------------


@app.post("/consent/check", response_model=ConsentCheckOut)
def consent_check(
    payload: ConsentCheckRequest, db: Session = Depends(get_db)
):
    """Check purpose-specific consent and persist an audit entry."""
    purpose = payload.purpose.strip().lower()
    if not purpose:
        raise HTTPException(status_code=422, detail="Purpose is required")
    decision = check_consent(db, payload.lead_id, purpose)
    result = "allowed" if decision.allowed else "blocked"
    audit = AuditLog(
        lead_id=payload.lead_id,
        actor=payload.actor,
        action_attempted=payload.action_attempted,
        purpose_required=purpose,
        consent_on_file=decision.consent_on_file,
        result=result,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return ConsentCheckOut(
        lead_id=payload.lead_id,
        purpose=purpose,
        allowed=decision.allowed,
        result=result,
        reason=decision.reason,
        consent_on_file=decision.consent_on_file,
        audit_log_id=audit.log_id,
        timestamp=audit.timestamp,
    )


@app.get("/audit-log", response_model=list[AuditLogOut])
def list_audit_logs(
    lead_id: Optional[str] = None,
    purpose: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Return recent consent checks for the activity feed."""
    query = db.query(AuditLog)
    if lead_id:
        query = query.filter(AuditLog.lead_id == lead_id)
    if purpose:
        query = query.filter(AuditLog.purpose_required == purpose.strip().lower())
    return query.order_by(AuditLog.timestamp.desc()).limit(limit).all()


# ---------------------------------------------------------------------------
# Interactions (agent actions)
# ---------------------------------------------------------------------------


@app.post("/leads/{lead_id}/interactions", response_model=InteractionOut, status_code=201)
async def log_interaction(
    lead_id: str, payload: InteractionCreate, db: Session = Depends(get_db)
):
    """Agent B logs what it did, including blocked attempts
    (action_type="no_action_consent_blocked", status="blocked"). Any logged
    interaction marks the lead as 'actioned', so it is not processed twice."""
    lead = _get_lead(db, lead_id)

    item = Interaction(lead_id=lead.id, **payload.model_dump())
    db.add(item)
    lead.last_activity_at = _now()
    db.commit()
    db.refresh(item)

    await manager.broadcast("interaction_logged", _interaction_json(item))
    return item


@app.get("/leads/{lead_id}/interactions", response_model=list[InteractionOut])
def list_lead_interactions(lead_id: str, db: Session = Depends(get_db)):
    _get_lead(db, lead_id)
    return (
        db.query(Interaction)
        .filter(Interaction.lead_id == lead_id)
        .order_by(Interaction.created_at.desc())
        .all()
    )


@app.get("/interactions", response_model=list[InteractionOut])
def list_interactions(
    status: Optional[InteractionStatus] = None,
    action_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Recent agent actions across all leads (feeds the activity panel;
    status=pending_approval gives the approval queue)."""
    query = db.query(Interaction)
    if status is not None:
        query = query.filter(Interaction.status == status)
    if action_type:
        query = query.filter(Interaction.action_type == action_type)
    return query.order_by(Interaction.created_at.desc()).limit(limit).all()


@app.patch("/interactions/{interaction_id}/status", response_model=InteractionOut)
async def update_interaction_status(
    interaction_id: str, payload: InteractionStatusUpdate, db: Session = Depends(get_db)
):
    """Approve / reject a drafted action, or mark it executed."""
    item = _get_interaction(db, interaction_id)
    item.status = payload.status
    db.commit()
    db.refresh(item)

    await manager.broadcast("interaction_updated", _interaction_json(item))
    return item


# ---------------------------------------------------------------------------
# Real-time feed
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """The dashboard connects here and receives every event listed above."""
    await manager.connect(websocket)
    try:
        while True:
            # nothing is expected from the client; this keeps the socket
            # open and lets us notice a disconnect
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/health")
def health():
    return {"status": "ok"}
