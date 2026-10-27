"""REST client for the Core CRM Backend (Role 2): the documented fallback path."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from lead_scoring.crm.base import (
    CRMRequestError,
    CRMUnavailableError,
    LeadNotFoundError,
    only_unscored,
)


class RESTCRMClient:
    name = "rest"

    def __init__(self, base_url: str, *, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def get_unscored_leads(self, limit: int) -> list[dict[str, Any]]:
        body = await self._request("GET", "/leads", params={"unscored": "true", "limit": limit})
        return only_unscored(body, limit)

    async def update_lead_score(self, lead_id: str, update: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PATCH", f"/leads/{quote(lead_id, safe='')}/score", json=update)

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
