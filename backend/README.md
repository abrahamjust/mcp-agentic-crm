# Backend: Core CRM (Role 2)

FastAPI + SQLite service. It is the only component that writes to the
database. Every other module (frontend, MCP layer, Agent A, Agent B,
consent layer) reads and writes leads through this API.

## Run

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- API: http://127.0.0.1:8000  (interactive docs at `/docs`)
- The database file `crm.db` is created next to the `app/` folder, so it is
  the same file wherever you start the server from. Set `CRM_DB_PATH` to use
  a different file.
- Run **one** server process (no `--workers N`): WebSocket broadcast is
  in-process, so extra workers would not see each other's connections.
- The dashboard is served at http://127.0.0.1:8000. Its pages are
  `/` (overview), `/pipeline`, `/approvals`, `/activity`, and `/leads/new`
  (lead form). No separate frontend server is required.
- To process leads end to end, also start Agent A and Agent B using the
  commands in [Agent A's guide](../agent_a/README.md) and
  [Agent B's guide](../agent_b/README.md). A lead remains Unscored until
  Agent A runs; follow-up drafts require a Hot score, marketing consent,
  and Agent B. Approving a draft records approval but does not send email.

## Test

```bash
cd backend
python -m pytest -q
```

Tests use a throwaway database and never touch `crm.db`.

## REST endpoints

| Method | Path | Used by | Purpose |
|---|---|---|---|
| POST | `/leads` | Frontend | Create a lead from raw form data; returns the standardized lead. `422` if name or email is invalid |
| GET | `/leads` | Everyone | List leads. Filters: `bucket`, `stage`, `unscored=true`, `actioned=true/false`; paging: `limit`, `offset` |
| GET | `/leads/{id}` | Everyone | One lead |
| PATCH | `/leads/{id}/score` | Agent A | Write `{score, bucket, top_factors, model_version}` (`update_lead_score`) |
| PATCH | `/leads/{id}/stage` | Agent B | Write `{pipeline_stage}` (`update_deal_stage`) |
| PATCH | `/leads/{id}/consent` | Frontend / Consent layer | Change consent for the listed purposes only: `{"consent": {"marketing": false}}` |
| POST | `/consent/check` | Agent B / Consent layer | Return an allow/block decision for a lead and purpose, and write its audit entry |
| GET | `/audit-log` | Frontend / Role 6 | List consent-check audit entries; filter by `lead_id` or `purpose` |
| GET | `/leads/stale` | Consent layer | Flag leads inactive beyond the configured retention period; does not delete |
| POST | `/leads/{id}/interactions` | Agent B | Log an action, including blocked ones (`log_interaction`) |
| GET | `/leads/{id}/interactions` | Everyone | Actions on one lead |
| GET | `/interactions` | Frontend | Recent actions; `status=pending_approval` is the approval queue; also `action_type`, `limit` |
| PATCH | `/interactions/{id}/status` | Frontend | Approve / reject / mark executed |
| GET | `/health` | Everyone | Liveness check |

### Scoring inputs (optional `POST /leads` fields)

Agent A's model uses these. All are optional: a blank one becomes
`Unknown` / `null` and the model still scores the lead.

| Field | Accepts | Stored as |
|---|---|---|
| `lead_origin` | `Landing Page`, `API`, `Lead Add Form`, `Quick Add Form`, `Lead Import` | `Landing Page Submission` / `API` / `Lead Add Form` / `Lead Import` / `Unknown` |
| `occupation` | `Working Professional`, `Business Owner`/`Businessman`, `Student`, `Unemployed`, `Homemaker`, `Other` | same names, or `Unknown` |
| `total_visits` | number >= 0 | integer |
| `time_on_website_sec` | number >= 0 (seconds) | integer |
| `pages_per_visit` | number >= 0 | float, 2 dp |

`source` also accepts web-analytics channel names (`Google`, `Organic Search`,
`Direct Traffic`, `Olark Chat`, `Reference`, ...). An existing `crm.db` gets
the new columns added automatically at startup (`add_missing_columns()`).

A lead is an individual learner enquiring about a course, not a company:
the old B2B fields (`company`, `company_size`, `job_title`, `seniority`)
were removed. `POST /leads` ignores them if an old client still sends them,
and `drop_removed_columns()` drops them from an existing `crm.db` at startup
(needs SQLite 3.35+). `product_interest` holds the course of interest.

Allowed `status` values for an interaction: `pending_approval`, `approved`,
`rejected`, `executed`, `blocked`, `skipped`. `consent_check`: `passed`,
`blocked`, `error`.

## WebSocket: `ws://127.0.0.1:8000/ws`

Every message is `{"event": "<name>", "data": {...}}`. `data` is the full
lead (or interaction) in the same JSON format the REST API returns
(ISO-8601 UTC timestamps, plain enum values).

| Event | Sent when | `data` |
|---|---|---|
| `lead_created` | a lead is created | lead |
| `lead_scored` | Agent A writes a score | lead |
| `lead_stage_changed` | pipeline stage changes | lead |
| `lead_consent_updated` | consent changes | lead |
| `interaction_logged` | an agent action is logged | interaction |
| `interaction_updated` | an interaction's status changes | interaction |

## Notes for each role

- **Role 1 (MCP):** tool mapping is
  `get_leads` -> `GET /leads`, `get_lead_by_id` -> `GET /leads/{id}`,
  `update_lead_score` -> `PATCH /leads/{id}/score`,
  `update_deal_stage` -> `PATCH /leads/{id}/stage`,
  `log_interaction` -> `POST /leads/{id}/interactions`.
  The request/response shapes are in `app/schemas.py`.
- **Role 3 (Agent A):** poll `GET /leads?unscored=true`, then PATCH the score.
  The model's inputs are the optional fields below; the training data goes
  through the same normalizers in `app/standardize.py`, so a value means the
  same thing in training and in live scoring. See `agent_a/README.md`.
- **Role 4 (Agent B):** after a restart, `GET /leads?bucket=Hot&actioned=false`
  returns the Hot leads nothing has been logged for yet. Log blocked attempts
  too (`action_type="no_action_consent_blocked"`, `status="blocked"`) so a
  blocked lead is not picked up again.
- **Role 5 (Consent):** `POST /consent/check` evaluates purpose-specific
  consent and writes an audit record. `GET /audit-log` returns those records.
  `GET /leads/stale` flags leads inactive beyond `CONSENT_RETENTION_DAYS`
  (90 by default), or an optional `days` query threshold. No lead is deleted.
- **Role 6 (Frontend):** the FastAPI app serves the dashboard and lead form.
  The overview shows CRM-backed KPIs and charts; pipeline, approval queue,
  and activity each have a separate page. Pages load `GET /leads`,
  interactions and consent audit events, then refresh on the WebSocket
  events above. The separate `/leads/new` form submits standardized lead
  input to `POST /leads`.

## Layout

```
backend/
  app/
    main.py         routes + WebSocket
    models.py       Lead, Interaction tables
    schemas.py      request/response shapes
    standardize.py  input cleaning (email, phone, source, occupation, consent)
    consent.py      purpose-specific consent decision logic (Role 5)
    events.py       WebSocket broadcast
    database.py     SQLite engine / session
  ../frontend/      dashboard, lead form and static assets served by FastAPI
  tests/            standardization, REST, WebSocket, DB upgrade
  requirements.txt
```
