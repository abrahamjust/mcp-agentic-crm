"""
MCP client for the CRM tool layer (Role 1): Agent B's primary path.

Uses these tools from the project's tool contract (report 5.5, Role 1):

    get_leads(filter)                        -> list of leads
    get_lead_by_id(lead_id)                  -> lead
    check_consent(lead_id, purpose)          -> allow/block decision (Role 5)
    log_interaction(lead_id, action_type, payload, status, consent_check)
    update_deal_stage(lead_id, new_stage)    -> updated lead
    list_interactions(status, action_type)   -> optional; approvals catch-up

Argument shapes are read from each tool's input schema at connect time
(filter wrapper or flat arguments; `new_stage`, `stage` or
`pipeline_stage`). get_leads and log_interaction are required: a server
without them is treated as unavailable. Any other tool that is missing, or
that cannot express what Agent B must send, raises ToolMissingError so
that one call goes over REST.

Two checks are strict because getting them wrong is unsafe rather than
just slower: get_leads must be able to filter on `actioned` (or every Hot
lead would be drafted again on every poll), and log_interaction must take
`status` (or a blocked attempt would be stored as pending_approval).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND, Implementation

from followup import __version__
from followup.crm.base import (
    CRMRequestError,
    CRMUnavailableError,
    LeadNotFoundError,
    ToolMissingError,
    check_consent_decision,
    only_hot,
    unwrap_list,
)
from followup.crm.rest import ACTOR

log = logging.getLogger(__name__)

_REQUEST_ERROR_CODES = {INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND}
_STAGE_ARGS = ("new_stage", "stage", "pipeline_stage")


@dataclass(frozen=True)
class ToolNames:
    get_leads: str = "get_leads"
    get_lead_by_id: str = "get_lead_by_id"
    check_consent: str = "check_consent"
    log_interaction: str = "log_interaction"
    update_deal_stage: str = "update_deal_stage"
    list_interactions: tuple[str, ...] = ("list_interactions", "get_interactions")


def stdio_server(command: str, args: list[str], cwd: str | None = None) -> StdioServerParameters:
    """Launch the MCP server as a subprocess. The SDK passes only a minimal
    environment by default; our own server needs ours (e.g. CRM_API_URL)."""
    return StdioServerParameters(command=command, args=args, env=dict(os.environ), cwd=cwd)


class MCPCRMClient:
    name = "mcp"

    def __init__(self, server: Any, *, timeout: float = 10.0, tools: ToolNames = ToolNames()):
        """`server`: an MCP endpoint URL (Streamable HTTP), StdioServerParameters,
        or an in-process MCPServer (tests)."""
        self._server = server
        self._timeout = timeout
        self._tools = tools
        self._client: Client | None = None
        self._schemas: dict[str, dict[str, Any]] = {}
        self._warned: set[str] = set()

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------

    async def get_pending_hot_leads(self, limit: int) -> list[dict[str, Any]]:
        await self._ensure_connected()
        leads = await self._call(self._tools.get_leads, self._get_leads_args(limit))
        return only_hot(leads, limit)

    async def get_lead(self, lead_id: str) -> dict[str, Any]:
        await self._ensure_connected()
        tool = self._require(self._tools.get_lead_by_id, {"lead_id"})
        return await self._call(tool, {"lead_id": lead_id})

    async def check_consent(self, lead_id: str, purpose: str, action_attempted: str) -> dict[str, Any]:
        await self._ensure_connected()
        tool = self._require(self._tools.check_consent, {"lead_id", "purpose"})
        args = self._accepted(tool, {"lead_id": lead_id, "purpose": purpose,
                                     "actor": ACTOR, "action_attempted": action_attempted})
        return check_consent_decision(await self._call(tool, args))

    async def log_interaction(self, lead_id: str, interaction: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_connected()
        tool = self._tools.log_interaction
        body = {"actor": ACTOR, **interaction}
        props = self._properties(tool)
        if props is not None:
            for wrapper in ("interaction", "data"):
                if wrapper in props:
                    return await self._call(tool, {"lead_id": lead_id, wrapper: body})
        self._require(tool, {"lead_id", "action_type", "payload", "status", "consent_check"})
        return await self._call(tool, self._accepted(tool, {"lead_id": lead_id, **body}))

    async def update_deal_stage(self, lead_id: str, stage: str) -> dict[str, Any]:
        await self._ensure_connected()
        tool = self._require(self._tools.update_deal_stage, {"lead_id"})
        props = self._properties(tool)
        arg = next((a for a in _STAGE_ARGS if props is None or a in props), None)
        if arg is None:
            raise self._missing(tool, f"{tool} takes none of {list(_STAGE_ARGS)}")
        return await self._call(tool, {"lead_id": lead_id, arg: stage})

    async def get_approved_drafts(self, limit: int) -> list[dict[str, Any]]:
        await self._ensure_connected()
        tool = next((t for t in self._tools.list_interactions if t in self._schemas), None)
        if tool is None:
            raise self._missing("list_interactions", "no interactions-listing tool")
        wanted = {"status": "approved", "action_type": "drafted_followup_email", "limit": limit}
        props = self._properties(tool)
        if props is not None and "status" not in props and "filter" not in props:
            raise self._missing(tool, f"{tool} cannot filter on status")
        args = {"filter": wanted} if props is not None and "filter" in props else self._accepted(tool, wanted)
        items = unwrap_list(await self._call(tool, args), "interactions")
        return [i for i in items if i.get("status") == "approved"
                and i.get("action_type") == "drafted_followup_email"]

    async def aclose(self) -> None:
        await self._reset()

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------

    async def _ensure_connected(self) -> None:
        if self._client is not None:
            return
        client = Client(
            self._server,
            read_timeout_seconds=self._timeout,
            client_info=Implementation(name="agent-b-followup", version=__version__),
        )
        try:
            await client.__aenter__()
        except Exception as exc:
            raise CRMUnavailableError(f"Cannot connect to MCP server: {exc!r}") from exc
        self._client = client
        try:
            listing = await client.list_tools()
        except Exception as exc:
            await self._reset()
            raise CRMUnavailableError(f"MCP tools/list failed: {exc!r}") from exc

        self._schemas = {tool.name: tool.input_schema or {} for tool in listing.tools}
        missing = [t for t in (self._tools.get_leads, self._tools.log_interaction) if t not in self._schemas]
        if missing:
            await self._reset()
            raise CRMUnavailableError(f"MCP server lacks Agent B's core tools: {missing}")
        log.info("Connected to MCP server; tools: %s", ", ".join(sorted(self._schemas)))

    async def _reset(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception:  # closing a broken session must never mask the original error
                log.debug("Error while closing MCP session", exc_info=True)

    # ------------------------------------------------------------------
    # schemas
    # ------------------------------------------------------------------

    def _properties(self, tool: str) -> dict[str, Any] | None:
        """The tool's declared arguments, or None if it accepts anything."""
        schema = self._schemas.get(tool, {})
        props = schema.get("properties")
        if props is None or schema.get("additionalProperties") is True:
            return None
        return props

    def _accepted(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        props = self._properties(tool)
        return args if props is None else {k: v for k, v in args.items() if k in props}

    def _require(self, tool: str, needed: set[str]) -> str:
        if tool not in self._schemas:
            raise self._missing(tool, f"no tool {tool!r}")
        props = self._properties(tool)
        if props is not None and not needed <= set(props):
            raise self._missing(tool, f"{tool} lacks arguments {sorted(needed - set(props))}")
        return tool

    def _missing(self, tool: str, reason: str) -> ToolMissingError:
        if tool not in self._warned:  # once per tool, not once per lead
            self._warned.add(tool)
            log.warning("MCP: %s; that operation goes over REST", reason)
        return ToolMissingError(reason)

    def _get_leads_args(self, limit: int) -> dict[str, Any]:
        wanted = {"bucket": "Hot", "actioned": False, "limit": limit}
        props = self._properties(self._tools.get_leads)
        if props is None:
            return wanted
        for wrapper in ("filter", "filters"):
            if wrapper in props:
                return {wrapper: wanted}
        if "actioned" not in props:
            raise self._missing(self._tools.get_leads, "get_leads cannot filter on 'actioned'")
        return {k: v for k, v in wanted.items() if k in props}  # bucket is re-checked client-side

    # ------------------------------------------------------------------
    # tool calls
    # ------------------------------------------------------------------

    async def _call(self, tool: str, args: dict[str, Any]) -> Any:
        assert self._client is not None
        try:
            result = await self._client.call_tool(tool, args)
        except MCPError as exc:
            if exc.code in _REQUEST_ERROR_CODES:
                raise CRMRequestError(f"{tool}: {exc.message}") from exc
            await self._reset()
            raise CRMUnavailableError(f"{tool}: MCP error {exc.code}: {exc.message}") from exc
        except Exception as exc:  # transport failure: closed stream, timeout, refused
            await self._reset()
            raise CRMUnavailableError(f"{tool}: {exc!r}") from exc

        text = "".join(getattr(c, "text", "") for c in result.content or [])
        if result.is_error:
            if "not found" in text.lower():
                raise LeadNotFoundError(f"{tool}: {text[:300]}")
            raise CRMRequestError(f"{tool}: {text[:300] or 'tool reported an error'}")

        data = result.structured_content
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]  # SDK wraps non-object return values
        if data is None:
            data = _parse_text_content(tool, result.content or [])
        return data


def _parse_text_content(tool: str, content: list[Any]) -> Any:
    """Servers without structured output return JSON as text; a list may
    arrive as one text block per item."""
    texts = [c.text for c in content if getattr(c, "type", None) == "text"]
    try:
        parsed = [json.loads(t) for t in texts]
    except ValueError as exc:
        raise CRMRequestError(f"{tool}: result is not JSON: {texts[:1]!r}") from exc
    if len(parsed) == 1:
        return parsed[0]
    return parsed
