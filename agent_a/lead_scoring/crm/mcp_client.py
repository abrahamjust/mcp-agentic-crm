"""
MCP client for the CRM tool layer (Role 1): Agent A's primary path.

Uses two tools from the project's tool contract:

    get_leads(filter)                                      -> list of leads
    update_lead_score(lead_id, score, bucket, top_factors) -> updated lead

The exact argument shape is read from each tool's input schema at connect
time, so both `get_leads(filter={"unscored": true})` and
`get_leads(unscored=true)` styles work, and `model_version` is sent only if
the tool accepts it. A server missing either tool is treated as unavailable,
which makes the fallback client switch to REST.
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

from lead_scoring import __version__
from lead_scoring.crm.base import (
    CRMRequestError,
    CRMUnavailableError,
    LeadNotFoundError,
    only_unscored,
)

log = logging.getLogger(__name__)

_REQUEST_ERROR_CODES = {INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND}


@dataclass(frozen=True)
class ToolNames:
    get_leads: str = "get_leads"
    update_lead_score: str = "update_lead_score"


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

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------

    async def get_unscored_leads(self, limit: int) -> list[dict[str, Any]]:
        await self._ensure_connected()
        args = self._get_leads_args(limit)
        leads = await self._call(self._tools.get_leads, args)
        return only_unscored(leads, limit)

    async def update_lead_score(self, lead_id: str, update: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_connected()
        props = self._properties(self._tools.update_lead_score)
        args = {"lead_id": lead_id, **update}
        if props is not None:
            args = {k: v for k, v in args.items() if k in props}
        return await self._call(self._tools.update_lead_score, args)

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
            client_info=Implementation(name="agent-a-lead-scoring", version=__version__),
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
        problems = self._check_contract()
        if problems:
            await self._reset()
            raise CRMUnavailableError("MCP server does not satisfy Agent A's tool contract: " + "; ".join(problems))
        log.info("Connected to MCP server; tools: %s", ", ".join(sorted(self._schemas)))

    def _check_contract(self) -> list[str]:
        problems = []
        for tool in (self._tools.get_leads, self._tools.update_lead_score):
            if tool not in self._schemas:
                problems.append(f"missing tool {tool!r}")
        props = self._properties(self._tools.update_lead_score)
        if self._tools.update_lead_score in self._schemas and props is not None:
            absent = {"lead_id", "score", "bucket", "top_factors"} - set(props)
            if absent:
                problems.append(f"{self._tools.update_lead_score} lacks arguments {sorted(absent)}")
        return problems

    async def _reset(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception:  # closing a broken session must never mask the original error
                log.debug("Error while closing MCP session", exc_info=True)

    # ------------------------------------------------------------------
    # tool calls
    # ------------------------------------------------------------------

    def _properties(self, tool: str) -> dict[str, Any] | None:
        """The tool's declared arguments, or None if it accepts anything."""
        schema = self._schemas.get(tool, {})
        props = schema.get("properties")
        if props is None or schema.get("additionalProperties") is True:
            return None
        return props

    def _get_leads_args(self, limit: int) -> dict[str, Any]:
        wanted = {"unscored": True, "limit": limit}
        props = self._properties(self._tools.get_leads)
        if props is None:
            return wanted
        for wrapper in ("filter", "filters"):
            if wrapper in props:
                return {wrapper: wanted}
        args = {k: v for k, v in wanted.items() if k in props}
        if "unscored" not in props and "bucket" in props:
            args["bucket"] = "Unscored"
        return args  # anything the tool cannot filter is filtered client-side

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
