"""MCP client against Role-1-style servers, REST client, and the fallback
policy between them."""
import httpx
import pytest

from fake_mcp import crm_server, scoring_only_server
from followup.agent import ACTION_BLOCKED, ACTION_DRAFT, FollowUpAgent
from followup.crm import (
    CRMRequestError,
    CRMUnavailableError,
    FallbackCRMClient,
    LeadNotFoundError,
    MCPCRMClient,
    RESTCRMClient,
    ToolMissingError,
)
from helpers import create_hot_lead, interactions

pytestmark = pytest.mark.anyio


async def _both_paths(backend_http, crm, template):
    """One consented and one denied Hot lead through Agent B."""
    yes = await create_hot_lead(backend_http, consent={"marketing": True})
    no = await create_hot_lead(backend_http, consent={"marketing": False})
    stats = await FollowUpAgent(crm, template).run_once()
    [a] = await interactions(backend_http, yes["id"])
    [b] = await interactions(backend_http, no["id"])
    return stats, a, b


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


async def test_full_tool_layer_end_to_end(backend_app, backend_http, template):
    server = crm_server(backend_app)
    crm = MCPCRMClient(server)
    try:
        stats, drafted, blocked = await _both_paths(backend_http, crm, template)
        assert (stats.drafted, stats.blocked) == (1, 1)
        assert (drafted["action_type"], drafted["status"]) == (ACTION_DRAFT, "pending_approval")
        assert (blocked["action_type"], blocked["status"]) == (ACTION_BLOCKED, "blocked")

        await backend_http.patch(f"/interactions/{drafted['id']}/status", json={"status": "approved"})
        assert (await FollowUpAgent(crm, template).run_once()).advanced == 1
    finally:
        await crm.aclose()
    for tool in ("get_leads", "check_consent", "log_interaction", "list_interactions",
                 "get_lead_by_id", "update_deal_stage"):
        assert tool in server.calls, tool


async def test_server_without_check_consent_uses_rest_for_that_call_only(backend_app, backend_http, rest_client, template):
    server = crm_server(backend_app, with_consent=False)
    crm = FallbackCRMClient(MCPCRMClient(server), rest_client)
    stats, drafted, blocked = await _both_paths(backend_http, crm, template)
    assert (stats.drafted, stats.blocked) == (1, 1)
    assert "log_interaction" in server.calls  # MCP stays in use for the rest
    assert crm.last_transport == "mcp"


async def test_log_interaction_without_status_is_not_used(backend_app, backend_http, rest_client, template):
    """Otherwise a blocked attempt would be stored as pending_approval."""
    server = crm_server(backend_app, with_interaction_status=False)
    crm = FallbackCRMClient(MCPCRMClient(server), rest_client)
    _, drafted, blocked = await _both_paths(backend_http, crm, template)
    assert "log_interaction" not in server.calls
    assert blocked["status"] == "blocked" and blocked["consent_check"] == "blocked"


async def test_get_leads_without_actioned_filter_is_not_used(backend_app, backend_http, rest_client, template):
    """Otherwise every Hot lead would be drafted again on every poll."""
    server = crm_server(backend_app, with_actioned_filter=False)
    crm = FallbackCRMClient(MCPCRMClient(server), rest_client)
    await _both_paths(backend_http, crm, template)
    assert "get_leads" not in server.calls
    assert (await FollowUpAgent(crm, template).run_once()).fetched == 0


async def test_mcp_only_without_list_tool_skips_approvals_quietly(backend_app, backend_http, template):
    crm = MCPCRMClient(crm_server(backend_app, with_list_interactions=False))
    try:
        with pytest.raises(ToolMissingError):
            await crm.get_approved_drafts(10)
        stats = await FollowUpAgent(crm, template).run_once()  # no crash
        assert stats.advanced == 0
    finally:
        await crm.aclose()


async def test_server_without_core_tools_is_unavailable():
    crm = MCPCRMClient(scoring_only_server())
    with pytest.raises(CRMUnavailableError, match="core tools"):
        await crm.get_pending_hot_leads(5)


async def test_unknown_lead_maps_to_not_found(backend_app):
    crm = MCPCRMClient(crm_server(backend_app))
    try:
        with pytest.raises(LeadNotFoundError):
            await crm.get_lead("missing")
    finally:
        await crm.aclose()


async def test_unreachable_mcp_falls_back_to_rest(backend_http, rest_client, template):
    crm = FallbackCRMClient(MCPCRMClient("http://127.0.0.1:9/mcp", timeout=1), rest_client)
    stats, _, _ = await _both_paths(backend_http, crm, template)
    assert (stats.drafted, stats.blocked) == (1, 1)
    assert crm.last_transport == "rest"


# ---------------------------------------------------------------------------
# fallback policy
# ---------------------------------------------------------------------------


class Fake:
    def __init__(self, name, exc=None):
        self.name, self.exc, self.calls = name, exc, 0

    async def get_lead(self, lead_id):
        self.calls += 1
        if self.exc:
            raise self.exc
        return {"id": lead_id, "via": self.name}

    async def aclose(self):
        pass


async def test_missing_tool_does_not_take_mcp_out_of_service():
    clock = [0.0]
    primary = Fake("mcp", ToolMissingError("no tool"))
    crm = FallbackCRMClient(primary, Fake("rest"), clock=lambda: clock[0])
    assert (await crm.get_lead("a"))["via"] == "rest"
    assert (await crm.get_lead("b"))["via"] == "rest"
    assert primary.calls == 2  # tried every time, not parked for 30 s


async def test_outage_parks_mcp_then_retries():
    clock = [0.0]
    primary = Fake("mcp", CRMUnavailableError("down"))
    crm = FallbackCRMClient(primary, Fake("rest"), retry_primary_after=30, clock=lambda: clock[0])
    await crm.get_lead("a")
    await crm.get_lead("b")
    assert primary.calls == 1
    clock[0] = 31
    primary.exc = None
    assert (await crm.get_lead("c"))["via"] == "mcp"


async def test_rejected_request_is_not_retried_over_rest():
    rest = Fake("rest")
    crm = FallbackCRMClient(Fake("mcp", CRMRequestError("bad")), rest)
    with pytest.raises(CRMRequestError):
        await crm.get_lead("a")
    assert rest.calls == 0


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


def _rest(handler) -> RESTCRMClient:
    return RESTCRMClient("http://crm", transport=httpx.MockTransport(handler))


async def test_rest_error_mapping():
    async def run(status):
        client = _rest(lambda r: httpx.Response(status, json={"detail": "x"}))
        try:
            await client.get_lead("a")
        finally:
            await client.aclose()

    with pytest.raises(CRMUnavailableError):
        await run(503)
    with pytest.raises(LeadNotFoundError):
        await run(404)
    with pytest.raises(CRMRequestError):
        await run(422)


async def test_rest_ignores_leads_that_are_not_hot():
    leads = [{"id": "1", "bucket": "Hot"}, {"id": "2", "bucket": "Warm"}, {"bucket": "Hot"}]
    client = _rest(lambda r: httpx.Response(200, json=leads))
    assert [lead["id"] for lead in await client.get_pending_hot_leads(10)] == ["1"]
    await client.aclose()


async def test_malformed_consent_decision_is_a_request_error():
    client = _rest(lambda r: httpx.Response(200, json={"result": "maybe"}))
    with pytest.raises(CRMRequestError, match="Malformed"):
        await client.check_consent("a", "marketing", "x")
    await client.aclose()
