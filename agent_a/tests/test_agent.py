"""The scoring agent end to end: backend <- (MCP | REST) <- agent <- model."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from fake_mcp import filter_style_server, incomplete_server
from helpers import FULL_FORM, create_lead
from lead_scoring.agent import ScoringAgent
from lead_scoring.crm import CRMRequestError, CRMUnavailableError, FallbackCRMClient, MCPCRMClient
from lead_scoring.crm.mcp_client import stdio_server

pytestmark = pytest.mark.anyio


async def test_rest_cycle_scores_every_new_lead(rest_client, backend_http, scorer, tmp_path):
    created = [
        await create_lead(backend_http, **FULL_FORM),
        await create_lead(backend_http, source="Google", lead_origin="API", occupation="Student",
                          total_visits=1, time_on_website_sec=15, pages_per_visit=1),
        await create_lead(backend_http),  # bare form: only name + email
    ]
    metrics = tmp_path / "metrics.jsonl"
    agent = ScoringAgent(rest_client, scorer, metrics_path=metrics)

    stats = await agent.run_once()
    assert (stats.fetched, stats.scored, stats.failed) == (3, 3, 0)

    for lead in created:
        stored = (await backend_http.get(f"/leads/{lead['id']}")).json()
        assert stored["bucket"] in ("Hot", "Warm", "Cold")
        assert 0 <= stored["lead_score"] <= 1
        assert stored["model_version"] == scorer.version
        assert stored["scored_at"] is not None
        assert isinstance(stored["top_factors"], list)

    strong = (await backend_http.get(f"/leads/{created[0]['id']}")).json()
    assert strong["bucket"] == "Hot"

    rows = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert len(rows) == 3
    assert all(r["creation_to_score_sec"] >= 0 and r["transport"] == "rest" for r in rows)

    # nothing left to do on the next cycle
    assert (await agent.run_once()).fetched == 0


async def test_mcp_cycle_goes_through_the_tool_layer(backend_app, backend_http, scorer):
    lead = await create_lead(backend_http, **FULL_FORM)
    server = filter_style_server(backend_app)
    client = MCPCRMClient(server)
    try:
        stats = await ScoringAgent(client, scorer).run_once()
    finally:
        await client.aclose()
    assert stats.scored == 1
    assert [name for name, _ in server.calls] == ["get_leads", "update_lead_score"]
    assert (await backend_http.get(f"/leads/{lead['id']}")).json()["bucket"] == "Hot"


async def test_mcp_over_stdio_subprocess(backend_http, scorer):
    """Real JSON-RPC over stdio to a separate server process."""
    lead = await create_lead(backend_http, **FULL_FORM)
    script = Path(__file__).with_name("stdio_mcp_server.py")
    client = MCPCRMClient(stdio_server(sys.executable, [str(script)]), timeout=60)
    try:
        stats = await ScoringAgent(client, scorer).run_once()
    finally:
        await client.aclose()
    assert stats.scored == 1
    stored = (await backend_http.get(f"/leads/{lead['id']}")).json()
    assert stored["bucket"] == "Hot"
    assert stored["model_version"] == scorer.version


async def test_broken_mcp_falls_back_to_rest(rest_client, backend_http, scorer):
    lead = await create_lead(backend_http, **FULL_FORM)
    crm = FallbackCRMClient(MCPCRMClient(incomplete_server()), rest_client)
    stats = await ScoringAgent(crm, scorer).run_once()
    assert stats.scored == 1
    assert crm.last_transport == "rest"
    assert (await backend_http.get(f"/leads/{lead['id']}")).json()["bucket"] != "Unscored"


async def test_websocket_sees_lead_scored_event(backend_app, rest_client, backend_http, scorer):
    """The dashboard learns about the score through the backend's broadcast."""
    from app.events import manager

    sent = []

    class Socket:
        async def send_text(self, message):
            sent.append(json.loads(message))

    socket = Socket()
    manager.active_connections.append(socket)
    try:
        await create_lead(backend_http, **FULL_FORM)
        await ScoringAgent(rest_client, scorer).run_once()
    finally:
        manager.disconnect(socket)
    events = [m["event"] for m in sent]
    assert events == ["lead_created", "lead_scored"]
    assert sent[1]["data"]["bucket"] == "Hot"
    assert sent[1]["data"]["top_factors"]


class FlakyCRM:
    """Wraps a client; writes for chosen lead ids raise a request error."""

    name = "flaky"

    def __init__(self, inner, bad_ids):
        self.inner, self.bad_ids = inner, set(bad_ids)

    async def get_unscored_leads(self, limit):
        return await self.inner.get_unscored_leads(limit)

    async def update_lead_score(self, lead_id, update):
        if lead_id in self.bad_ids:
            raise CRMRequestError("rejected")
        return await self.inner.update_lead_score(lead_id, update)

    async def aclose(self):
        pass


async def test_one_bad_lead_does_not_block_the_others(rest_client, backend_http, scorer):
    bad = await create_lead(backend_http)
    good = await create_lead(backend_http)
    agent = ScoringAgent(FlakyCRM(rest_client, {bad["id"]}), scorer, max_attempts=2)

    stats = await agent.run_once()
    assert (stats.scored, stats.failed) == (1, 1)
    assert (await backend_http.get(f"/leads/{good['id']}")).json()["bucket"] != "Unscored"

    await agent.run_once()  # second failure: set aside
    assert bad["id"] in agent.given_up
    assert (await agent.run_once()).fetched == 0  # no longer retried


class DownCRM:
    name = "down"

    def __init__(self):
        self.calls = 0

    async def get_unscored_leads(self, limit):
        self.calls += 1
        raise CRMUnavailableError("connection refused")

    async def update_lead_score(self, lead_id, update):
        raise CRMUnavailableError("connection refused")

    async def aclose(self):
        pass


async def test_unavailable_crm_propagates_from_a_cycle(scorer):
    with pytest.raises(CRMUnavailableError):
        await ScoringAgent(DownCRM(), scorer).run_once()


async def test_run_loop_backs_off_and_stops_cleanly(scorer):
    crm, stop = DownCRM(), asyncio.Event()
    agent = ScoringAgent(crm, scorer)
    task = asyncio.create_task(agent.run(poll_interval=0.01, max_backoff=0.04, stop=stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    # exponential backoff: far fewer attempts than 0.3s / 0.01s
    assert 2 <= crm.calls < 15


async def test_wakeup_event_triggers_an_immediate_cycle(rest_client, backend_http, scorer):
    stop, wakeup = asyncio.Event(), asyncio.Event()
    agent = ScoringAgent(rest_client, scorer)
    task = asyncio.create_task(agent.run(poll_interval=60, stop=stop, wakeup=wakeup))
    await asyncio.sleep(0.2)  # first (empty) cycle done; now sleeping up to 60s

    lead = await create_lead(backend_http, **FULL_FORM)
    wakeup.set()
    for _ in range(100):
        if (await backend_http.get(f"/leads/{lead['id']}")).json()["bucket"] != "Unscored":
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert (await backend_http.get(f"/leads/{lead['id']}")).json()["bucket"] == "Hot"
