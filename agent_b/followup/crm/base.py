"""The operations Agent B needs from the CRM, and the errors they raise."""
from __future__ import annotations

from typing import Any, Protocol


class CRMError(Exception):
    """Base class for CRM access errors."""


class CRMUnavailableError(CRMError):
    """The CRM could not be reached (connection refused, timeout, 5xx, broken
    MCP session). Worth retrying later or through another transport."""


class ToolMissingError(CRMUnavailableError):
    """The MCP server is up but does not offer this operation (or offers it
    with arguments Agent B cannot use). The operation goes over REST; the
    other MCP tools keep being used."""


class CRMRequestError(CRMError):
    """The CRM was reached and rejected this request. Retrying the same
    request through another transport will not help."""


class LeadNotFoundError(CRMRequestError):
    """The lead no longer exists (e.g. deleted between fetch and action)."""


class CRMClient(Protocol):
    name: str

    async def get_pending_hot_leads(self, limit: int) -> list[dict[str, Any]]:
        """Hot leads with no logged interaction yet, at most `limit`."""

    async def get_lead(self, lead_id: str) -> dict[str, Any]:
        """One lead (`get_lead_by_id`)."""

    async def check_consent(self, lead_id: str, purpose: str, action_attempted: str) -> dict[str, Any]:
        """Role 5's decision: {allowed, result, reason, audit_log_id, ...}.
        Writes one audit-log entry per call."""

    async def log_interaction(self, lead_id: str, interaction: dict[str, Any]) -> dict[str, Any]:
        """Record {action_type, payload, status, consent_check, actor}; returns it."""

    async def update_deal_stage(self, lead_id: str, stage: str) -> dict[str, Any]:
        """Move the lead to `stage`; returns the lead."""

    async def get_approved_drafts(self, limit: int) -> list[dict[str, Any]]:
        """Follow-up drafts a person has approved, newest first."""

    async def aclose(self) -> None:
        """Release connections."""


def unwrap_list(value: Any, what: str = "leads") -> list[dict[str, Any]]:
    """Normalize a list result; a tool may wrap it ({"leads": [...]})."""
    if isinstance(value, dict):
        for key in (what, "items", "result", "data"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list):
        raise CRMRequestError(f"Expected a list of {what}, got {type(value).__name__}")
    return [item for item in value if isinstance(item, dict) and item.get("id")]


def only_hot(leads: Any, limit: int) -> list[dict[str, Any]]:
    """Defensive on purpose: a tool that ignores the bucket filter must not
    make Agent B email a Warm or Cold lead."""
    return [lead for lead in unwrap_list(leads) if lead.get("bucket") == "Hot"][:limit]


def check_consent_decision(decision: Any) -> dict[str, Any]:
    """A consent decision Agent B can act on. Anything malformed is a
    request error, which the agent treats as blocked (fail closed)."""
    if not isinstance(decision, dict) or not isinstance(decision.get("allowed"), bool):
        raise CRMRequestError(f"Malformed consent decision: {str(decision)[:200]}")
    return decision
