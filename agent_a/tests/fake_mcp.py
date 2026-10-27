"""
Stand-ins for Role 1's MCP tool layer, wrapping the real backend.

They follow the tool contract in the project report (get_leads(filter),
update_lead_score(lead_id, score, bucket, top_factors)) so Agent A's MCP
client is tested against the same shapes it will meet in integration, in
both argument styles a server might reasonably choose.
"""
from __future__ import annotations

from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


def _http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://crm")


async def _patch_score(app, lead_id: str, body: dict[str, Any]) -> dict[str, Any]:
    async with _http(app) as http:
        response = await http.patch(f"/leads/{lead_id}/score", json=body)
    if response.status_code == 404:
        raise ToolError(f"Lead {lead_id} not found")
    if response.status_code >= 400:
        raise ToolError(f"Backend rejected the score: {response.text}")
    return response.json()


def filter_style_server(app, *, with_model_version: bool = True) -> MCPServer:
    """get_leads(filter: dict) and update_lead_score(..., model_version?)."""
    server = MCPServer("crm-tools")
    calls: list[tuple[str, dict]] = []
    server.calls = calls  # type: ignore[attr-defined]

    @server.tool()
    async def get_leads(filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """List leads. filter: {bucket, stage, unscored, actioned, limit}."""
        calls.append(("get_leads", {"filter": filter}))
        async with _http(app) as http:
            response = await http.get("/leads", params={k: v for k, v in (filter or {}).items() if v is not None})
        return response.json()

    if with_model_version:
        @server.tool()
        async def update_lead_score(
            lead_id: str, score: float, bucket: str, top_factors: list[str], model_version: str = "v1"
        ) -> dict[str, Any]:
            calls.append(("update_lead_score", {"lead_id": lead_id, "model_version": model_version}))
            return await _patch_score(app, lead_id, {"score": score, "bucket": bucket,
                                                     "top_factors": top_factors, "model_version": model_version})
    else:
        @server.tool()
        async def update_lead_score(lead_id: str, score: float, bucket: str, top_factors: list[str]) -> dict[str, Any]:
            calls.append(("update_lead_score", {"lead_id": lead_id}))
            return await _patch_score(app, lead_id, {"score": score, "bucket": bucket, "top_factors": top_factors})

    return server


def flat_style_server(app) -> MCPServer:
    """get_leads(unscored: bool, limit: int): filters as top-level arguments."""
    server = MCPServer("crm-tools-flat")
    calls: list[tuple[str, dict]] = []
    server.calls = calls  # type: ignore[attr-defined]

    @server.tool()
    async def get_leads(unscored: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        calls.append(("get_leads", {"unscored": unscored, "limit": limit}))
        params = {"limit": limit, **({"unscored": "true"} if unscored else {})}
        async with _http(app) as http:
            return (await http.get("/leads", params=params)).json()

    @server.tool()
    async def update_lead_score(
        lead_id: str, score: float, bucket: str, top_factors: list[str], model_version: str = "v1"
    ) -> dict[str, Any]:
        calls.append(("update_lead_score", {"lead_id": lead_id}))
        return await _patch_score(app, lead_id, {"score": score, "bucket": bucket,
                                                 "top_factors": top_factors, "model_version": model_version})

    return server


def incomplete_server() -> MCPServer:
    """Has get_leads but not update_lead_score."""
    server = MCPServer("crm-tools-incomplete")

    @server.tool()
    async def get_leads(filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    return server
