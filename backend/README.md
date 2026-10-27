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
  Your dataset's columns must be mapped onto the standardized fields in
  `app/standardize.py`.
- **Role 4 (Agent B):** after a restart, `GET /leads?bucket=Hot&actioned=false`
  returns the Hot leads nothing has been logged for yet. Log blocked attempts
  too (`action_type="no_action_consent_blocked"`, `status="blocked"`) so a
  blocked lead is not picked up again.
- **Role 5 (Consent):** `POST /consent/check` evaluates purpose-specific
  consent and writes an audit record. `GET /audit-log` returns those records.
  `GET /leads/stale` flags leads inactive beyond `CONSENT_RETENTION_DAYS`
  (90 by default), or an optional `days` query threshold. No lead is deleted.
- **Role 6 (Frontend):** CORS is open for local development. Load the board
  with `GET /leads`, then keep it live from the WebSocket events above.

## Layout

```
backend/
  app/
    main.py         routes + WebSocket
    models.py       Lead, Interaction tables
    schemas.py      request/response shapes
    standardize.py  input cleaning (email, phone, size, seniority, source, consent)
    consent.py      purpose-specific consent decision logic (Role 5)
    events.py       WebSocket broadcast
    database.py     SQLite engine / session
  tests/            54 tests (standardization, REST, WebSocket)
  requirements.txt
```
