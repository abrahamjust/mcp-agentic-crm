"""
Optional HTTP prediction service: the scorer as a callable endpoint.

    uvicorn lead_scoring.service:app --port 8002

    GET  /health   liveness + loaded model version
    GET  /model    model summary (features, thresholds, test metrics)
    POST /score    standardized lead record -> {score, bucket, top_factors, model_version}

Useful if the MCP layer wants a `score_lead` tool or for a quick manual
check. It does not write to the CRM; the agent does that.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from lead_scoring.predictor import LeadScorer


class LeadIn(BaseModel):
    """The scoring-relevant subset of a backend lead (LeadOut); other fields
    are accepted and ignored, so a full lead record can be posted as-is."""

    model_config = ConfigDict(extra="allow")

    id: Optional[str] = None
    lead_origin: Optional[str] = None
    source: Optional[str] = None
    occupation: Optional[str] = None
    total_visits: Optional[float] = Field(None, ge=0)
    time_on_website_sec: Optional[float] = Field(None, ge=0)
    pages_per_visit: Optional[float] = Field(None, ge=0)


class ScoreOut(BaseModel):
    lead_id: Optional[str]
    score: float
    bucket: str
    top_factors: list[str]
    model_version: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scorer = LeadScorer.load()
    yield


app = FastAPI(title="Agent A: lead scoring (Role 3)", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "model_version": app.state.scorer.version}


@app.get("/model")
def model_info():
    return app.state.scorer.info()


@app.post("/score", response_model=ScoreOut)
async def score(lead: LeadIn):
    result = await run_in_threadpool(app.state.scorer.score, lead.model_dump())
    return result.as_dict()
