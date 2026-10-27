"""
MCP first, REST when MCP is unreachable (design doc 5.3: "MCP layer is
unavailable -> agents fall back to calling Role 2's REST endpoints").

Only CRMUnavailableError triggers the fallback; a request the CRM rejected
would be rejected over REST too. After a failure the primary is skipped for
`retry_primary_after` seconds, then tried again. Writes are idempotent, so
repeating one over the fallback after an ambiguous failure is safe.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from lead_scoring.crm.base import CRMClient, CRMUnavailableError

log = logging.getLogger(__name__)


class FallbackCRMClient:
    def __init__(
        self,
        primary: CRMClient,
        fallback: CRMClient,
        *,
        retry_primary_after: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.primary = primary
        self.fallback = fallback
        self.retry_primary_after = retry_primary_after
        self._clock = clock
        self._primary_down_until = 0.0
        self.last_transport: str | None = None

    @property
    def name(self) -> str:
        return f"{self.primary.name}->{self.fallback.name}"

    async def get_unscored_leads(self, limit: int) -> list[dict[str, Any]]:
        return await self._call("get_unscored_leads", limit)

    async def update_lead_score(self, lead_id: str, update: dict[str, Any]) -> dict[str, Any]:
        return await self._call("update_lead_score", lead_id, update)

    async def aclose(self) -> None:
        for client in (self.primary, self.fallback):
            try:
                await client.aclose()
            except Exception:
                log.debug("Error closing %s client", client.name, exc_info=True)

    async def _call(self, op: str, *args: Any) -> Any:
        if self._clock() >= self._primary_down_until:
            try:
                result = await getattr(self.primary, op)(*args)
            except CRMUnavailableError as exc:
                self._primary_down_until = self._clock() + self.retry_primary_after
                log.warning(
                    "%s unavailable (%s); using %s, retrying %s in %.0fs",
                    self.primary.name, exc, self.fallback.name, self.primary.name, self.retry_primary_after,
                )
            else:
                if self.last_transport == self.fallback.name:
                    log.info("%s is back; switched from %s", self.primary.name, self.fallback.name)
                self.last_transport = self.primary.name
                return result

        result = await getattr(self.fallback, op)(*args)
        self.last_transport = self.fallback.name
        return result
