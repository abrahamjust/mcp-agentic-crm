"""
MCP first, REST when MCP is unreachable (design doc 5.3: "MCP layer is
unavailable -> agents fall back to calling Role 2's REST endpoints").

Only CRMUnavailableError triggers the fallback; a request the CRM rejected
would be rejected over REST too. After a failure the primary is skipped for
`retry_primary_after` seconds, then tried again. A ToolMissingError (the
MCP server is fine but lacks that one tool) sends only that call to REST
and leaves the primary in use for everything else.

Retrying a write over REST after an ambiguous MCP failure could log an
interaction twice; that is preferred to losing it (NFR: "failed or blocked
agent actions never disappear silently").
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from followup.crm.base import CRMClient, CRMUnavailableError, ToolMissingError

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

    async def get_pending_hot_leads(self, limit: int) -> list[dict[str, Any]]:
        return await self._call("get_pending_hot_leads", limit)

    async def get_lead(self, lead_id: str) -> dict[str, Any]:
        return await self._call("get_lead", lead_id)

    async def check_consent(self, lead_id: str, purpose: str, action_attempted: str) -> dict[str, Any]:
        return await self._call("check_consent", lead_id, purpose, action_attempted)

    async def log_interaction(self, lead_id: str, interaction: dict[str, Any]) -> dict[str, Any]:
        return await self._call("log_interaction", lead_id, interaction)

    async def update_deal_stage(self, lead_id: str, stage: str) -> dict[str, Any]:
        return await self._call("update_deal_stage", lead_id, stage)

    async def get_approved_drafts(self, limit: int) -> list[dict[str, Any]]:
        return await self._call("get_approved_drafts", limit)

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
            except ToolMissingError as exc:
                log.debug("%s: %s; using %s for this call", self.primary.name, exc, self.fallback.name)
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
