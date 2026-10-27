"""MCP tool layer for the CRM REST API.

Run from the repository root with ``python -m mcp_server.server``. The MCP
transport is stdio; the CRM backend remains the only database writer.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


CRM_API_URL = os.environ.get("CRM_API_URL", "http://127.0.0.1:8000").rstrip("/")
server = MCPServer("agentic-crm-tools")


async def _request(method: str, path: str, *, params: dict[str, Any] | None = None,
                   body: dict[str, Any] | None = None) -> Any:
    """Call the existing backend and surface API errors as MCP tool errors."""
    try:
        async with httpx.AsyncClient(base_url=CRM_API_URL, timeout=15.0) as client:
            response = await client.request(method, path, params=params, json=body)
    except httpx.HTTPError as exc:
        raise ToolError(f"CRM backend unavailable at {CRM_API_URL}: {exc}") from exc

    if response.is_error:
        if response.status_code == 404:
            detail = "Lead not found"
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise ToolError(str(detail))
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ToolError(f"CRM backend returned {response.status_code}: {detail}")
    return response.json()


@server.tool()
async def get_leads(
    bucket: str | None = None,
    stage: str | None = None,
    unscored: bool | None = None,
    actioned: bool | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List CRM leads. Filters: bucket, stage, unscored, actioned, limit, offset."""
    params = {
        key: value for key, value in {
            "bucket": bucket, "stage": stage, "unscored": unscored,
            "actioned": actioned, "limit": limit, "offset": offset,
        }.items() if value is not None
    }
    return await _request("GET", "/leads", params=params)


@server.tool()
async def get_lead_by_id(lead_id: str) -> dict[str, Any]:
    """Get a single lead by its CRM ID."""
    return await _request("GET", f"/leads/{lead_id}")


@server.tool()
async def update_lead_score(
    lead_id: str,
    score: float,
    bucket: str,
    top_factors: list[str],
    model_version: str = "v1",
) -> dict[str, Any]:
    """Write Agent A's score, bucket, reasons, and model version to the CRM."""
    return await _request(
        "PATCH", f"/leads/{lead_id}/score",
        body={"score": score, "bucket": bucket, "top_factors": top_factors,
              "model_version": model_version},
    )


@server.tool()
async def update_deal_stage(lead_id: str, pipeline_stage: str) -> dict[str, Any]:
    """Move a lead to a pipeline stage supported by the CRM."""
    return await _request(
        "PATCH", f"/leads/{lead_id}/stage", body={"pipeline_stage": pipeline_stage}
    )


@server.tool()
async def log_interaction(
    lead_id: str,
    action_type: str,
    payload: dict[str, Any] | None = None,
    actor: str = "agent_b",
    status: str = "pending_approval",
    consent_check: str | None = None,
) -> dict[str, Any]:
    """Record an agent action, including a blocked attempt, against a lead."""
    body: dict[str, Any] = {
        "actor": actor, "action_type": action_type, "payload": payload or {},
        "status": status,
    }
    if consent_check is not None:
        body["consent_check"] = consent_check
    return await _request("POST", f"/leads/{lead_id}/interactions", body=body)


@server.tool()
async def check_consent(
    lead_id: str,
    purpose: str,
    actor: str = "agent_b",
    action_attempted: str = "consent_check",
) -> dict[str, Any]:
    """Check purpose-specific consent; the backend records an audit entry."""
    return await _request(
        "POST", "/consent/check",
        body={"lead_id": lead_id, "purpose": purpose, "actor": actor,
              "action_attempted": action_attempted},
    )


if __name__ == "__main__":
    server.run("stdio")
