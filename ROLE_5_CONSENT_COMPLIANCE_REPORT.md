# Role 5: Consent and Compliance Layer

**Name:** Mohammed Ashlab  
**Registration number:** 23MIA1081  
**Role:** Consent and Compliance Layer  
**Verification date:** 27 October 2026

## 1. Scope

This work implements only Role 5 functionality on top of the existing Core CRM
Backend (Role 2). Existing lead, scoring, pipeline, interaction, and WebSocket
behavior was preserved.

Role 5 depends on the lead schema and consent information already provided by
Role 2. Consent remains stored as purpose-specific records on each lead:

```json
[
  {
    "purpose": "marketing",
    "granted": true,
    "timestamp": "2026-10-27T15:00:00+00:00"
  }
]
```

## 2. Deliverables completed

| Requirement | Implementation | Status |
|---|---|---|
| Purpose-specific consent model | Existing `Lead.consent` JSON list stores `{purpose, granted, timestamp}` records. Consent updates replace only the requested purpose and refresh its timestamp. | Complete |
| `check_consent(lead_id, purpose)` | Added reusable decision function in [`backend/app/consent.py`](backend/app/consent.py). It normalizes the purpose, checks the lead’s records, uses the latest matching record, and returns an allow/block decision with a reason. | Complete |
| Allow granted consent | `marketing=true` returns `allowed=true`, `result="allowed"`, and a granted reason. | Complete |
| Block denied consent | `marketing=false` returns `allowed=false`, `result="blocked"`, and a denied reason. | Complete |
| Audit trail for every check | Added `AuditLog` model with `log_id`, `lead_id`, `actor`, `action_attempted`, `purpose_required`, `consent_on_file`, `result`, and `timestamp`. | Complete |
| Consent-check API | Added `POST /consent/check`, returning the decision, reason, consent status, audit ID, and timestamp. | Complete |
| Audit-log API | Added `GET /audit-log`, with optional `lead_id`, `purpose`, and `limit` filters. | Complete |
| Retention/stale check | Added `GET /leads/stale`; default threshold is 90 days, configurable through `CONSENT_RETENTION_DAYS` or the `days` query parameter. It flags records and does not delete leads. | Complete |
| Tests | Added tests for allowed and blocked marketing consent, structured audit entries, and stale-lead detection. | Complete |

## 3. API contracts

### Consent check

```http
POST /consent/check
Content-Type: application/json
```

Request:

```json
{
  "lead_id": "lead-id",
  "purpose": "marketing",
  "actor": "agent_b",
  "action_attempted": "drafted_followup_email"
}
```

Allowed response:

```json
{
  "lead_id": "lead-id",
  "purpose": "marketing",
  "allowed": true,
  "result": "allowed",
  "reason": "Consent granted for purpose 'marketing'",
  "consent_on_file": true,
  "audit_log_id": "audit-id",
  "timestamp": "2026-10-27T15:00:00Z"
}
```

Blocked response:

```json
{
  "lead_id": "lead-id",
  "purpose": "marketing",
  "allowed": false,
  "result": "blocked",
  "reason": "Consent denied for purpose 'marketing'",
  "consent_on_file": true,
  "audit_log_id": "audit-id",
  "timestamp": "2026-10-27T15:00:00Z"
}
```

Missing consent also fails closed with `allowed=false`. The check is still
audited, with `consent_on_file=false`, so every consent-check attempt remains
traceable.

### Audit log

```http
GET /audit-log
GET /audit-log?lead_id=<lead-id>
GET /audit-log?purpose=marketing
```

The endpoint returns newest records first and is suitable for consumption by
Role 6’s activity feed.

### Retention check

```http
GET /leads/stale
GET /leads/stale?days=90
```

The environment variable `CONSENT_RETENTION_DAYS` controls the default when no
`days` query parameter is supplied. The endpoint only identifies stale leads;
it does not delete or alter them.

## 4. Files changed

- [`backend/app/consent.py`](backend/app/consent.py): consent decision logic.
- [`backend/app/models.py`](backend/app/models.py): `AuditLog` ORM model.
- [`backend/app/schemas.py`](backend/app/schemas.py): Role 5 request and response schemas.
- [`backend/app/main.py`](backend/app/main.py): consent-check, audit-log, and stale-lead routes.
- [`backend/tests/test_api.py`](backend/tests/test_api.py): Role 5 API tests.
- [`backend/README.md`](backend/README.md): endpoint and Role 5 documentation.

## 5. Verification

Commands executed:

```bash
cd backend
python -m pytest -q
python -m compileall -q app
```

Results:

- **57 tests passed**
- **Python compilation passed**
- One pre-existing dependency warning from the FastAPI/Starlette test client was
  reported; it did not affect test results.

## 6. Final compliance assessment

The implementation satisfies the requested Role 5 deliverables. It is limited
to the consent/compliance layer and does not implement unrelated Role 1, Role
3, Role 4, or Role 6 functionality. Consent decisions fail closed for denied,
missing, or invalid consent, and every API consent-check attempt creates an
audit-log record.
