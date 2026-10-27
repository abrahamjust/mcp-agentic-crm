"""End-to-end API tests (REST + WebSocket) against a throwaway database."""


def make_lead(client, **overrides):
    payload = {"name": "Test Lead", "email": "test@example.com"}
    payload.update(overrides)
    resp = client.post("/leads", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# basics + leads
# ---------------------------------------------------------------------------


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_lead_standardizes_and_returns_lead(client):
    body = make_lead(
        client,
        name="john   DOE",
        email="John@Gmail.COM ",
        phone="9876543210",
        company="Acme Corp",
        company_size="~75",
        job_title="VP of Sales",
        source="fb ad",
        product_interest="Enterprise plan",
        notes="",
        consent={"marketing": True, "analytics": False},
    )
    assert body["name"] == "John Doe"
    assert body["email"] == "john@gmail.com"
    assert body["phone"] == "+919876543210"
    assert body["company_size"] == "51-200"
    assert body["seniority"] == "Director"
    assert body["source"] == "Social"
    assert body["notes"] is None
    assert body["pipeline_stage"] == "New"
    assert body["bucket"] == "Unscored"
    assert body["lead_score"] is None
    consent_map = {c["purpose"]: c["granted"] for c in body["consent"]}
    assert consent_map == {"marketing": True, "analytics": False}


def test_create_lead_with_scoring_inputs(client):
    body = make_lead(
        client,
        lead_origin="landing page",
        occupation="working professional",
        total_visits=4,
        time_on_website_sec="930",
        pages_per_visit=2.456,
    )
    assert body["lead_origin"] == "Landing Page Submission"
    assert body["occupation"] == "Working Professional"
    assert body["total_visits"] == 4
    assert body["time_on_website_sec"] == 930
    assert body["pages_per_visit"] == 2.46


def test_create_lead_scoring_inputs_default_to_unknown(client):
    body = make_lead(client)
    assert body["lead_origin"] == "Unknown"
    assert body["occupation"] == "Unknown"
    assert body["total_visits"] is None
    assert body["time_on_website_sec"] is None
    assert body["pages_per_visit"] is None


def test_create_lead_negative_engagement_is_422(client):
    resp = client.post(
        "/leads", json={"name": "Jane", "email": "jane@example.com", "total_visits": -2}
    )
    assert resp.status_code == 422


def test_create_lead_invalid_email_is_422_not_500(client):
    resp = client.post("/leads", json={"name": "Jane", "email": "notanemail"})
    assert resp.status_code == 422


def test_create_lead_blank_name_is_422_not_500(client):
    resp = client.post("/leads", json={"name": "   ", "email": "jane@example.com"})
    assert resp.status_code == 422


def test_get_lead_by_id_and_404(client):
    lead = make_lead(client, email="jane@example.com")
    assert client.get(f"/leads/{lead['id']}").json()["email"] == "jane@example.com"
    assert client.get("/leads/does-not-exist").status_code == 404


def test_list_leads_filter_unscored(client):
    make_lead(client, email="a@example.com")
    make_lead(client, email="b@example.com")
    leads = client.get("/leads", params={"unscored": True}).json()
    assert len(leads) == 2
    assert all(l["bucket"] == "Unscored" for l in leads)


def test_list_leads_invalid_bucket_is_422(client):
    assert client.get("/leads", params={"bucket": "hot"}).status_code == 422


def test_list_leads_pagination(client):
    for i in range(5):
        make_lead(client, email=f"user{i}@example.com")
    assert len(client.get("/leads", params={"limit": 2}).json()) == 2
    assert len(client.get("/leads", params={"limit": 10, "offset": 3}).json()) == 2


def test_timestamps_are_timezone_aware_utc(client):
    lead = make_lead(client)
    assert lead["created_at"].endswith(("Z", "+00:00"))
    assert "T" in lead["created_at"]


def test_cors_preflight_allows_frontend_origin(client):
    resp = client.options(
        "/leads",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] in ("*", "http://localhost:3000")


# ---------------------------------------------------------------------------
# agent write-back
# ---------------------------------------------------------------------------


def test_update_lead_score_agent_a_contract(client):
    lead = make_lead(client)
    resp = client.patch(
        f"/leads/{lead['id']}/score",
        json={
            "score": 0.82,
            "bucket": "Hot",
            "top_factors": ["Director-level title", "Website source"],
            "model_version": "v1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["lead_score"] == 0.82
    assert body["bucket"] == "Hot"
    assert body["top_factors"] == ["Director-level title", "Website source"]
    assert body["scored_at"] is not None

    hot = client.get("/leads", params={"bucket": "Hot"}).json()
    assert any(l["id"] == lead["id"] for l in hot)


def test_update_lead_score_rejects_out_of_range(client):
    lead = make_lead(client)
    resp = client.patch(
        f"/leads/{lead['id']}/score", json={"score": 1.5, "bucket": "Hot"}
    )
    assert resp.status_code == 422


def test_update_lead_stage_agent_b_contract(client):
    lead = make_lead(client)
    resp = client.patch(f"/leads/{lead['id']}/stage", json={"pipeline_stage": "Contacted"})
    assert resp.status_code == 200
    assert resp.json()["pipeline_stage"] == "Contacted"
    stage_filtered = client.get("/leads", params={"stage": "Contacted"}).json()
    assert [l["id"] for l in stage_filtered] == [lead["id"]]


def test_score_nonexistent_lead_404(client):
    resp = client.patch(
        "/leads/nope/score", json={"score": 0.5, "bucket": "Warm", "top_factors": []}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# consent updates
# ---------------------------------------------------------------------------


def test_update_consent_changes_only_listed_purposes(client):
    lead = make_lead(client, consent={"marketing": True, "analytics": True})
    resp = client.patch(f"/leads/{lead['id']}/consent", json={"consent": {"marketing": False}})
    assert resp.status_code == 200
    consent_map = {c["purpose"]: c["granted"] for c in resp.json()["consent"]}
    assert consent_map == {"marketing": False, "analytics": True}


def test_update_consent_can_add_a_new_purpose(client):
    lead = make_lead(client)  # no consent at all
    resp = client.patch(f"/leads/{lead['id']}/consent", json={"consent": {"support": True}})
    consent_map = {c["purpose"]: c["granted"] for c in resp.json()["consent"]}
    assert consent_map == {"support": True}


def test_update_consent_empty_body_is_422(client):
    lead = make_lead(client)
    assert client.patch(f"/leads/{lead['id']}/consent", json={"consent": {}}).status_code == 422


def test_update_consent_unknown_lead_404(client):
    assert client.patch("/leads/nope/consent", json={"consent": {"marketing": True}}).status_code == 404


# ---------------------------------------------------------------------------
# interactions (log_interaction)
# ---------------------------------------------------------------------------


def test_log_interaction_and_list_for_lead(client):
    lead = make_lead(client)
    resp = client.post(
        f"/leads/{lead['id']}/interactions",
        json={
            "action_type": "drafted_followup_email",
            "payload": {"subject": "Hello", "body": "Hi there"},
            "consent_check": "passed",
        },
    )
    assert resp.status_code == 201
    item = resp.json()
    assert item["lead_id"] == lead["id"]
    assert item["actor"] == "agent_b"
    assert item["status"] == "pending_approval"
    assert item["payload"]["subject"] == "Hello"

    listed = client.get(f"/leads/{lead['id']}/interactions").json()
    assert [i["id"] for i in listed] == [item["id"]]


def test_log_interaction_updates_lead_last_activity(client):
    lead = make_lead(client)
    client.post(f"/leads/{lead['id']}/interactions", json={"action_type": "x"})
    after = client.get(f"/leads/{lead['id']}").json()
    assert after["last_activity_at"] >= lead["last_activity_at"]


def test_log_interaction_unknown_lead_404(client):
    resp = client.post("/leads/nope/interactions", json={"action_type": "x"})
    assert resp.status_code == 404


def test_log_interaction_rejects_bad_status(client):
    lead = make_lead(client)
    resp = client.post(
        f"/leads/{lead['id']}/interactions",
        json={"action_type": "x", "status": "banana"},
    )
    assert resp.status_code == 422


def test_actioned_filter_finds_leads_with_no_interaction(client):
    """Agent B's catch-up query after a restart: Hot leads not yet handled."""
    handled = make_lead(client, email="handled@example.com")
    pending = make_lead(client, email="pending@example.com")
    for lead in (handled, pending):
        client.patch(f"/leads/{lead['id']}/score", json={"score": 0.9, "bucket": "Hot"})
    client.post(
        f"/leads/{handled['id']}/interactions",
        json={"action_type": "no_action_consent_blocked", "status": "blocked",
              "consent_check": "blocked"},
    )

    todo = client.get("/leads", params={"bucket": "Hot", "actioned": False}).json()
    assert [l["id"] for l in todo] == [pending["id"]]

    done = client.get("/leads", params={"actioned": True}).json()
    assert [l["id"] for l in done] == [handled["id"]]


def test_update_interaction_status_approve_flow(client):
    lead = make_lead(client)
    item = client.post(
        f"/leads/{lead['id']}/interactions", json={"action_type": "drafted_followup_email"}
    ).json()

    resp = client.patch(f"/interactions/{item['id']}/status", json={"status": "approved"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    queue = client.get("/interactions", params={"status": "pending_approval"}).json()
    assert queue == []


def test_update_interaction_status_404(client):
    resp = client.patch("/interactions/nope/status", json={"status": "approved"})
    assert resp.status_code == 404


def test_list_all_interactions_filters_by_action_type(client):
    lead = make_lead(client)
    client.post(f"/leads/{lead['id']}/interactions", json={"action_type": "a"})
    client.post(f"/leads/{lead['id']}/interactions", json={"action_type": "b"})
    only_a = client.get("/interactions", params={"action_type": "a"}).json()
    assert len(only_a) == 1 and only_a[0]["action_type"] == "a"
    assert len(client.get("/interactions").json()) == 2


# ---------------------------------------------------------------------------
# websocket events
# ---------------------------------------------------------------------------


def test_websocket_lead_created_event(client):
    with client.websocket_connect("/ws") as ws:
        client.post("/leads", json={"name": "Live Feed", "email": "live@example.com"})
        message = ws.receive_json()
        assert message["event"] == "lead_created"
        assert message["data"]["email"] == "live@example.com"


def test_websocket_payload_format_matches_rest(client):
    """Same ISO timestamps and enum values over WebSocket as over REST."""
    with client.websocket_connect("/ws") as ws:
        rest = client.post("/leads", json={"name": "Fmt", "email": "fmt@example.com"}).json()
        data = ws.receive_json()["data"]
        assert data["created_at"] == rest["created_at"]
        assert data["bucket"] == "Unscored"
        assert data["company_size"] == "Unknown"


def test_websocket_lead_scored_event(client):
    lead = make_lead(client)
    with client.websocket_connect("/ws") as ws:
        client.patch(f"/leads/{lead['id']}/score", json={"score": 0.9, "bucket": "Hot"})
        message = ws.receive_json()
        assert message["event"] == "lead_scored"
        assert message["data"]["bucket"] == "Hot"


def test_websocket_stage_and_consent_events(client):
    lead = make_lead(client)
    with client.websocket_connect("/ws") as ws:
        client.patch(f"/leads/{lead['id']}/stage", json={"pipeline_stage": "Qualified"})
        assert ws.receive_json()["event"] == "lead_stage_changed"
        client.patch(f"/leads/{lead['id']}/consent", json={"consent": {"marketing": True}})
        assert ws.receive_json()["event"] == "lead_consent_updated"


def test_websocket_interaction_events(client):
    lead = make_lead(client)
    with client.websocket_connect("/ws") as ws:
        item = client.post(
            f"/leads/{lead['id']}/interactions", json={"action_type": "drafted_followup_email"}
        ).json()
        logged = ws.receive_json()
        assert logged["event"] == "interaction_logged"
        assert logged["data"]["id"] == item["id"]

        client.patch(f"/interactions/{item['id']}/status", json={"status": "approved"})
        updated = ws.receive_json()
        assert updated["event"] == "interaction_updated"
        assert updated["data"]["status"] == "approved"
