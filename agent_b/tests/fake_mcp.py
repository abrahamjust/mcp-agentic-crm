"""
Stand-ins for Role 1's MCP tool layer, wrapping the real backend.

They follow the tool contract in the project report (get_leads(filter),
get_lead_by_id, check_consent(lead_id, purpose), log_interaction,
update_deal_stage(lead_id, new_stage)), so Agent B's MCP client is tested
against the shapes it will meet in integration, plus the incomplete
variants a first version of the server might plausibly ship.
"""
from __future__ import annotations

from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


def _http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://crm")


async def _send(app, method: str, path: str, **kwargs) -> Any:
    async with _http(app) as http:
        response = await http.request(method, path, **kwargs)
    if response.status_code == 404:
        raise ToolError(f"Lead not found: {path}")
    if response.status_code >= 400:
        raise ToolError(f"Backend rejected {method} {path}: {response.text}")
    return response.json()


def crm_server(
    app,
    *,
    with_consent: bool = True,
    with_interaction_status: bool = True,
    with_actioned_filter: bool = True,
    with_list_interactions: bool = True,
) -> MCPServer:
    server = MCPServer("crm-tools")
    calls: list[str] = []
    server.calls = calls  # type: ignore[attr-defined]

    if with_actioned_filter:
        @server.tool()
        async def get_leads(filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            """List leads. filter: {bucket, stage, unscored, actioned, limit}."""
            calls.append("get_leads")
            params = {k: str(v).lower() if isinstance(v, bool) else v for k, v in (filter or {}).items()}
            return await _send(app, "GET", "/leads", params=params)
    else:
        @server.tool()
        async def get_leads(bucket: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
            calls.append("get_leads")
            return await _send(app, "GET", "/leads", params={"limit": limit, **({"bucket": bucket} if bucket else {})})

    @server.tool()
    async def get_lead_by_id(lead_id: str) -> dict[str, Any]:
        calls.append("get_lead_by_id")
        return await _send(app, "GET", f"/leads/{lead_id}")

    if with_consent:
        @server.tool()
        async def check_consent(lead_id: str, purpose: str, actor: str = "agent_b",
                                action_attempted: str = "consent_check") -> dict[str, Any]:
            calls.append("check_consent")
            return await _send(app, "POST", "/consent/check", json={
                "lead_id": lead_id, "purpose": purpose, "actor": actor, "action_attempted": action_attempted})

    if with_interaction_status:
        @server.tool()
        async def log_interaction(lead_id: str, action_type: str, payload: dict[str, Any], status: str,
                                  consent_check: str | None = None, actor: str = "agent_b") -> dict[str, Any]:
            calls.append("log_interaction")
            return await _send(app, "POST", f"/leads/{lead_id}/interactions", json={
                "action_type": action_type, "payload": payload, "status": status,
                "consent_check": consent_check, "actor": actor})
    else:
        @server.tool()
        async def log_interaction(lead_id: str, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append("log_interaction")
            return await _send(app, "POST", f"/leads/{lead_id}/interactions",
                               json={"action_type": action_type, "payload": payload})

    @server.tool()
    async def update_deal_stage(lead_id: str, new_stage: str) -> dict[str, Any]:
        calls.append("update_deal_stage")
        return await _send(app, "PATCH", f"/leads/{lead_id}/stage", json={"pipeline_stage": new_stage})

    if with_list_interactions:
        @server.tool()
        async def list_interactions(status: str | None = None, action_type: str | None = None,
                                    limit: int = 100) -> list[dict[str, Any]]:
            calls.append("list_interactions")
            params = {k: v for k, v in {"status": status, "action_type": action_type, "limit": limit}.items() if v}
            return await _send(app, "GET", "/interactions", params=params)

    return server


def scoring_only_server() -> MCPServer:
    """Agent A's tools only: not usable by Agent B."""
    server = MCPServer("crm-tools-agent-a")

    @server.tool()
    async def get_leads(filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    return server
