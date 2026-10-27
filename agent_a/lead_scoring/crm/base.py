"""The two operations Agent A needs from the CRM, and the errors they raise."""
from __future__ import annotations

from typing import Any, Protocol


class CRMError(Exception):
    """Base class for CRM access errors."""


class CRMUnavailableError(CRMError):
    """The CRM could not be reached (connection refused, timeout, 5xx, broken
    MCP session). Worth retrying later or through another transport."""


class CRMRequestError(CRMError):
    """The CRM was reached and rejected this request. Retrying the same
    request through another transport will not help."""


class LeadNotFoundError(CRMRequestError):
    """The lead no longer exists (e.g. deleted between fetch and write-back)."""


class CRMClient(Protocol):
    name: str

    async def get_unscored_leads(self, limit: int) -> list[dict[str, Any]]:
        """Leads with bucket "Unscored", newest first, at most `limit`."""

    async def update_lead_score(self, lead_id: str, update: dict[str, Any]) -> dict[str, Any]:
        """Write {score, bucket, top_factors, model_version}; returns the lead.
        Idempotent: writing the same score twice is harmless."""

    async def aclose(self) -> None:
        """Release connections."""


def only_unscored(leads: Any, limit: int) -> list[dict[str, Any]]:
    """Normalize a get_leads result to a list of unscored lead dicts.

    Defensive on purpose: a tool may wrap the list ({"leads": [...]}) or
    ignore the unscored filter, and scoring an already-scored lead would
    overwrite a newer score.
    """
    if isinstance(leads, dict):
        for key in ("leads", "items", "result", "data"):
            if isinstance(leads.get(key), list):
                leads = leads[key]
                break
    if not isinstance(leads, list):
        raise CRMRequestError(f"Expected a list of leads, got {type(leads).__name__}")
    unscored = [
        lead for lead in leads
        if isinstance(lead, dict) and lead.get("id") and lead.get("bucket", "Unscored") == "Unscored"
    ]
    return unscored[:limit]
