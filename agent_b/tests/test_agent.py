"""Agent B against the real backend: consent gate, audit trail, approval
boundary, idempotency and failure handling."""
import pytest

from followup.agent import ACTION_BLOCKED, ACTION_DRAFT, FollowUpAgent, is_wakeup_event
from followup.crm import CRMRequestError, CRMUnavailableError
from followup.drafting import DraftError
from helpers import audits, create_hot_lead, interactions

pytestmark = pytest.mark.anyio


class FlakyCRM:
    """Wraps a real client and makes chosen operations fail."""

    def __init__(self, inner, **failures):
        self.inner, self.failures, self.name = inner, failures, "flaky"

    def __getattr__(self, op):
        target = getattr(self.inner, op)
        if op not in self.failures:
            return target

        async def failing(*args):
            exc = self.failures[op]
            if exc is None:
                return await target(*args)
            raise exc
        return failing


class BrokenDrafter:
    name = "broken"

    def __init__(self):
        self.calls = 0

    async def draft(self, lead):
        self.calls += 1
        raise DraftError("model offline")


# ---------------------------------------------------------------------------
# the consent gate
# ---------------------------------------------------------------------------


async def test_consented_hot_lead_gets_a_pending_draft(backend_http, rest_client, template):
    lead = await create_hot_lead(backend_http, name="Ananya Iyer", consent={"marketing": True})
    stats = await FollowUpAgent(rest_client, template).run_once()
    assert (stats.fetched, stats.drafted, stats.blocked) == (1, 1, 0)

    [item] = await interactions(backend_http, lead["id"])
    assert item["actor"] == "agent_b"
    assert item["action_type"] == ACTION_DRAFT
    assert item["status"] == "pending_approval"
    assert item["consent_check"] == "passed"
    email = item["payload"]["email"]
    assert email["to"] == lead["email"] and email["body"].startswith("Hi Ananya,")
    assert item["payload"]["purpose"] == "marketing"
    assert item["payload"]["reviewer_context"]["bucket"] == "Hot"
    assert "acted_at" in item["payload"] and "suggested_next_step" in item["payload"]

    [audit] = await audits(backend_http, lead["id"])
    assert audit["result"] == "allowed" and audit["actor"] == "agent_b"
    assert audit["action_attempted"] == ACTION_DRAFT and audit["purpose_required"] == "marketing"
    assert item["payload"]["audit_log_id"] == audit["log_id"]

    stage = (await backend_http.get(f"/leads/{lead['id']}")).json()["pipeline_stage"]
    assert stage == "New"  # nothing advances before a person approves


@pytest.mark.parametrize("consent, reason", [
    ({"marketing": False}, "Consent denied for purpose 'marketing'"),
    ({"analytics": True}, "No consent record exists for purpose 'marketing'"),
    (None, "No consent record exists for purpose 'marketing'"),
])
async def test_lead_without_marketing_consent_is_blocked_and_recorded(backend_http, rest_client, template, consent, reason):
    lead = await create_hot_lead(backend_http, consent=consent)
    stats = await FollowUpAgent(rest_client, template).run_once()
    assert (stats.drafted, stats.blocked) == (0, 1)

    [item] = await interactions(backend_http, lead["id"])
    assert item["action_type"] == ACTION_BLOCKED
    assert item["status"] == "blocked" and item["consent_check"] == "blocked"
    assert item["payload"]["reason"] == reason
    assert "email" not in item["payload"]

    [audit] = await audits(backend_http, lead["id"])
    assert audit["result"] == "blocked"
    assert item["payload"]["audit_log_id"] == audit["log_id"]


async def test_drafter_never_sees_a_blocked_lead(backend_http, rest_client):
    class Spy:
        name = "spy"
        seen = []

        async def draft(self, lead):
            self.seen.append(lead["id"])
            raise AssertionError("must not be called")

    await create_hot_lead(backend_http, consent={"marketing": False})
    spy = Spy()
    await FollowUpAgent(rest_client, spy).run_once()
    assert spy.seen == []


async def test_consent_is_checked_at_the_point_of_action(backend_http, rest_client, template):
    """Consent withdrawn after the lead was created (and scored) still blocks."""
    lead = await create_hot_lead(backend_http, consent={"marketing": True})
    await backend_http.patch(f"/leads/{lead['id']}/consent", json={"consent": {"marketing": False}})
    await FollowUpAgent(rest_client, template).run_once()
    [item] = await interactions(backend_http, lead["id"])
    assert item["action_type"] == ACTION_BLOCKED


async def test_unusable_consent_answer_fails_closed(backend_http, rest_client, template):
    lead = await create_hot_lead(backend_http, consent={"marketing": True})
    crm = FlakyCRM(rest_client, check_consent=CRMRequestError("Malformed consent decision"))
    stats = await FollowUpAgent(crm, template).run_once()
    assert stats.blocked == 1 and stats.drafted == 0
    [item] = await interactions(backend_http, lead["id"])
    assert item["status"] == "blocked" and item["consent_check"] == "error"
    assert "Malformed consent decision" in item["payload"]["reason"]


async def test_unreachable_crm_does_nothing_and_retries_later(backend_http, rest_client, template):
    lead = await create_hot_lead(backend_http, consent={"marketing": True})
    crm = FlakyCRM(rest_client, check_consent=CRMUnavailableError("down"))
    with pytest.raises(CRMUnavailableError):
        await FollowUpAgent(crm, template).run_once()
    assert await interactions(backend_http, lead["id"]) == []

    stats = await FollowUpAgent(rest_client, template).run_once()  # e.g. after a restart
    assert stats.drafted == 1


# ---------------------------------------------------------------------------
# which leads, and only once
# ---------------------------------------------------------------------------


async def test_only_hot_leads_are_handled(backend_http, rest_client, template):
    for bucket in ("Warm", "Cold"):
        await create_hot_lead(backend_http, bucket=bucket, consent={"marketing": True})
    unscored = (await backend_http.post("/leads", json={"name": "New One", "email": "new@example.com",
                                                        "consent": {"marketing": True}})).json()
    stats = await FollowUpAgent(rest_client, template).run_once()
    assert stats.fetched == 0
    assert await interactions(backend_http, unscored["id"]) == []


async def test_each_lead_is_handled_once(backend_http, rest_client, template):
    await create_hot_lead(backend_http, consent={"marketing": True})
    await create_hot_lead(backend_http, consent={"marketing": False})
    agent = FollowUpAgent(rest_client, template)
    first = await agent.run_once()
    assert (first.drafted, first.blocked) == (1, 1)
    second = await agent.run_once()
    restarted = await FollowUpAgent(rest_client, template).run_once()
    assert second.fetched == restarted.fetched == 0
    assert len((await backend_http.get("/interactions")).json()) == 2
    assert len((await backend_http.get("/audit-log")).json()) == 2


async def test_batch_size_limits_a_cycle(backend_http, rest_client, template):
    for _ in range(3):
        await create_hot_lead(backend_http, consent={"marketing": True})
    agent = FollowUpAgent(rest_client, template, batch_size=2)
    assert (await agent.run_once()).drafted == 2
    assert (await agent.run_once()).drafted == 1


async def test_failed_draft_leaves_lead_for_retry_then_gives_up(backend_http, rest_client):
    lead = await create_hot_lead(backend_http, consent={"marketing": True})
    drafter = BrokenDrafter()
    agent = FollowUpAgent(rest_client, drafter, max_attempts=2)
    assert (await agent.run_once()).failed == 1
    assert await interactions(backend_http, lead["id"]) == []  # not actioned: retried
    assert (await agent.run_once()).failed == 1
    assert agent.given_up == {lead["id"]}
    assert (await agent.run_once()).fetched == 0
    assert drafter.calls == 2


# ---------------------------------------------------------------------------
# pipeline stage and the approval boundary
# ---------------------------------------------------------------------------


async def _draft_for(backend_http, rest_client, template, **kwargs):
    lead = await create_hot_lead(backend_http, consent={"marketing": True})
    agent = FollowUpAgent(rest_client, template, **kwargs)
    await agent.run_once()
    [item] = await interactions(backend_http, lead["id"])
    return lead, item, agent


async def _stage(backend_http, lead_id):
    return (await backend_http.get(f"/leads/{lead_id}")).json()["pipeline_stage"]


async def test_approved_draft_moves_lead_to_contacted(backend_http, rest_client, template):
    lead, item, agent = await _draft_for(backend_http, rest_client, template)
    await backend_http.patch(f"/interactions/{item['id']}/status", json={"status": "approved"})
    stats = await agent.run_once()
    assert stats.advanced == 1
    assert await _stage(backend_http, lead["id"]) == "Contacted"
    assert (await agent.run_once()).advanced == 0


async def test_rejected_draft_leaves_lead_in_new(backend_http, rest_client, template):
    lead, item, agent = await _draft_for(backend_http, rest_client, template)
    await backend_http.patch(f"/interactions/{item['id']}/status", json={"status": "rejected"})
    assert (await agent.run_once()).advanced == 0
    assert await _stage(backend_http, lead["id"]) == "New"


async def test_approval_never_moves_a_lead_backwards(backend_http, rest_client, template):
    lead, item, agent = await _draft_for(backend_http, rest_client, template)
    await backend_http.patch(f"/leads/{lead['id']}/stage", json={"pipeline_stage": "Qualified"})
    await backend_http.patch(f"/interactions/{item['id']}/status", json={"status": "approved"})
    assert (await FollowUpAgent(rest_client, template).run_once()).advanced == 0
    assert await _stage(backend_http, lead["id"]) == "Qualified"


async def test_on_draft_policy_advances_immediately(backend_http, rest_client, template):
    lead, item, _ = await _draft_for(backend_http, rest_client, template, stage_policy="on_draft")
    assert await _stage(backend_http, lead["id"]) == "Contacted"
    assert item["status"] == "pending_approval"  # the email itself still waits for a person


async def test_never_policy_leaves_stage_alone(backend_http, rest_client, template):
    lead, item, agent = await _draft_for(backend_http, rest_client, template, stage_policy="never")
    await backend_http.patch(f"/interactions/{item['id']}/status", json={"status": "approved"})
    await agent.run_once()
    assert await _stage(backend_http, lead["id"]) == "New"


def test_unknown_stage_policy_is_rejected(template):
    with pytest.raises(ValueError):
        FollowUpAgent(None, template, stage_policy="always")


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message, wake", [
    ({"event": "lead_scored", "data": {"bucket": "Hot"}}, True),
    ({"event": "lead_scored", "data": {"bucket": "Warm"}}, False),
    ({"event": "interaction_updated", "data": {"status": "approved", "action_type": ACTION_DRAFT}}, True),
    ({"event": "interaction_updated", "data": {"status": "rejected", "action_type": ACTION_DRAFT}}, False),
    ({"event": "lead_created", "data": {}}, False),
    ("not a dict", False),
])
def test_wakeup_events(message, wake):
    assert is_wakeup_event(message) is wake
