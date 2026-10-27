"""CRM clients: REST against the real backend, MCP against Role-1-style servers."""
import httpx
import pytest

from fake_mcp import filter_style_server, flat_style_server, incomplete_server
from helpers import create_lead
from lead_scoring.crm import (
    CRMRequestError,
    CRMUnavailableError,
    FallbackCRMClient,
    LeadNotFoundError,
    MCPCRMClient,
    RESTCRMClient,
)
from lead_scoring.crm.base import only_unscored

pytestmark = pytest.mark.anyio

UPDATE = {"score": 0.7, "bucket": "Hot", "top_factors": ["Source: Referral (raises score)"], "model_version": "test-v1"}


# ---------------------------------------------------------------------------
# REST (fallback path) against the in-process backend
# ---------------------------------------------------------------------------


async def test_rest_lists_only_unscored_leads(rest_client, backend_http):
    a = await create_lead(backend_http)
    b = await create_lead(backend_http)
    await backend_http.patch(f"/leads/{b['id']}/score", json={"score": 0.1, "bucket": "Cold"})

    leads = await rest_client.get_unscored_leads(limit=10)
    assert [lead["id"] for lead in leads] == [a["id"]]


async def test_rest_respects_limit(rest_client, backend_http):
    for _ in range(3):
        await create_lead(backend_http)
    assert len(await rest_client.get_unscored_leads(limit=2)) == 2


async def test_rest_writes_score(rest_client, backend_http):
    lead = await create_lead(backend_http)
    updated = await rest_client.update_lead_score(lead["id"], UPDATE)
    assert updated["bucket"] == "Hot"
    assert updated["lead_score"] == 0.7
    assert updated["model_version"] == "test-v1"
    assert updated["scored_at"] is not None


async def test_rest_unknown_lead_is_not_found(rest_client):
    with pytest.raises(LeadNotFoundError):
        await rest_client.update_lead_score("no-such-lead", UPDATE)


async def test_rest_invalid_body_is_a_request_error(rest_client, backend_http):
    lead = await create_lead(backend_http)
    with pytest.raises(CRMRequestError):
        await rest_client.update_lead_score(lead["id"], {**UPDATE, "score": 1.5})


async def test_rest_connection_failure_is_unavailable():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = RESTCRMClient("http://crm", transport=httpx.MockTransport(refuse))
    with pytest.raises(CRMUnavailableError):
        await client.get_unscored_leads(10)
    await client.aclose()


async def test_rest_server_error_is_unavailable():
    client = RESTCRMClient("http://crm", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(CRMUnavailableError):
        await client.get_unscored_leads(10)
    await client.aclose()


def test_only_unscored_unwraps_and_filters():
    leads = [{"id": "1", "bucket": "Unscored"}, {"id": "2", "bucket": "Hot"}, {"no": "id"}]
    assert [lead["id"] for lead in only_unscored({"leads": leads}, 10)] == ["1"]
    with pytest.raises(CRMRequestError):
        only_unscored("not a list", 10)


# ---------------------------------------------------------------------------
# MCP (primary path)
# ---------------------------------------------------------------------------


async def test_mcp_filter_style_round_trip(backend_app, backend_http):
    lead = await create_lead(backend_http)
    server = filter_style_server(backend_app)
    client = MCPCRMClient(server)
    try:
        leads = await client.get_unscored_leads(limit=5)
        assert [x["id"] for x in leads] == [lead["id"]]
        updated = await client.update_lead_score(lead["id"], UPDATE)
        assert updated["bucket"] == "Hot"
        assert updated["model_version"] == "test-v1"
    finally:
        await client.aclose()
    assert server.calls[0] == ("get_leads", {"filter": {"unscored": True, "limit": 5}})


async def test_mcp_flat_style_round_trip(backend_app, backend_http):
    lead = await create_lead(backend_http)
    server = flat_style_server(backend_app)
    client = MCPCRMClient(server)
    try:
        assert [x["id"] for x in await client.get_unscored_leads(limit=5)] == [lead["id"]]
        await client.update_lead_score(lead["id"], UPDATE)
    finally:
        await client.aclose()
    assert server.calls[0] == ("get_leads", {"unscored": True, "limit": 5})


async def test_mcp_omits_model_version_if_tool_does_not_accept_it(backend_app, backend_http):
    lead = await create_lead(backend_http)
    client = MCPCRMClient(filter_style_server(backend_app, with_model_version=False))
    try:
        updated = await client.update_lead_score(lead["id"], UPDATE)
    finally:
        await client.aclose()
    assert updated["bucket"] == "Hot"  # accepted, without the unknown argument


async def test_mcp_not_found_from_tool(backend_app):
    client = MCPCRMClient(filter_style_server(backend_app))
    try:
        with pytest.raises(LeadNotFoundError):
            await client.update_lead_score("no-such-lead", UPDATE)
    finally:
        await client.aclose()


async def test_mcp_server_missing_a_tool_is_unavailable():
    client = MCPCRMClient(incomplete_server())
    with pytest.raises(CRMUnavailableError, match="update_lead_score"):
        await client.get_unscored_leads(5)
    await client.aclose()


async def test_mcp_unreachable_url_is_unavailable():
    client = MCPCRMClient("http://127.0.0.1:9/mcp", timeout=2)
    with pytest.raises(CRMUnavailableError):
        await client.get_unscored_leads(5)
    await client.aclose()


# ---------------------------------------------------------------------------
# fallback policy
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self, name, error=None):
        self.name = name
        self.error = error
        self.calls = 0
        self.closed = False

    async def get_unscored_leads(self, limit):
        self.calls += 1
        if self.error:
            raise self.error
        return [{"id": self.name, "bucket": "Unscored"}]

    async def update_lead_score(self, lead_id, update):
        self.calls += 1
        if self.error:
            raise self.error
        return {"id": lead_id}

    async def aclose(self):
        self.closed = True


async def test_fallback_used_when_primary_unavailable():
    now = [0.0]
    primary, rest = FakeClient("mcp", CRMUnavailableError("down")), FakeClient("rest")
    client = FallbackCRMClient(primary, rest, retry_primary_after=30, clock=lambda: now[0])

    assert (await client.get_unscored_leads(1))[0]["id"] == "rest"
    assert client.last_transport == "rest"
    await client.get_unscored_leads(1)
    assert primary.calls == 1  # cooling down: not retried yet

    now[0] = 31.0
    primary.error = None
    assert (await client.get_unscored_leads(1))[0]["id"] == "mcp"
    assert client.last_transport == "mcp"


async def test_request_errors_do_not_fall_back():
    primary, rest = FakeClient("mcp", CRMRequestError("bad request")), FakeClient("rest")
    client = FallbackCRMClient(primary, rest)
    with pytest.raises(CRMRequestError):
        await client.update_lead_score("x", UPDATE)
    assert rest.calls == 0


async def test_fallback_closes_both_clients():
    primary, rest = FakeClient("mcp"), FakeClient("rest")
    await FallbackCRMClient(primary, rest).aclose()
    assert primary.closed and rest.closed
