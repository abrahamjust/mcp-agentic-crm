"""REST client for the Core CRM Backend (Role 2): the documented fallback path."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from followup.crm.base import (
    CRMRequestError,
    CRMUnavailableError,
    LeadNotFoundError,
    check_consent_decision,
    only_hot,
    unwrap_list,
)

ACTOR = "agent_b"


class RESTCRMClient:
    name = "rest"

    def __init__(self, base_url: str, *, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def get_pending_hot_leads(self, limit: int) -> list[dict[str, Any]]:
        body = await self._request("GET", "/leads", params={"bucket": "Hot", "actioned": "false", "limit": limit})
        return only_hot(body, limit)

    async def get_lead(self, lead_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/leads/{quote(lead_id, safe='')}")

    async def check_consent(self, lead_id: str, purpose: str, action_attempted: str) -> dict[str, Any]:
        body = {"lead_id": lead_id, "purpose": purpose, "actor": ACTOR, "action_attempted": action_attempted}
        return check_consent_decision(await self._request("POST", "/consent/check", json=body))

    async def log_interaction(self, lead_id: str, interaction: dict[str, Any]) -> dict[str, Any]:
        body = {"actor": ACTOR, **interaction}
        return await self._request("POST", f"/leads/{quote(lead_id, safe='')}/interactions", json=body)

    async def update_deal_stage(self, lead_id: str, stage: str) -> dict[str, Any]:
        return await self._request("PATCH", f"/leads/{quote(lead_id, safe='')}/stage", json={"pipeline_stage": stage})

    async def get_approved_drafts(self, limit: int) -> list[dict[str, Any]]:
        body = await self._request(
            "GET", "/interactions",
            params={"status": "approved", "action_type": "drafted_followup_email", "limit": limit},
        )
        return unwrap_list(body, "interactions")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise CRMUnavailableError(f"{method} {self.base_url}{path}: {exc!r}") from exc

        if response.status_code >= 500:
            raise CRMUnavailableError(f"{method} {path}: HTTP {response.status_code}")
        if response.status_code == 404:
            raise LeadNotFoundError(f"{method} {path}: not found")
        if response.status_code >= 400:
            raise CRMRequestError(f"{method} {path}: HTTP {response.status_code} {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise CRMRequestError(f"{method} {path}: response is not JSON") from exc
