# Agent B: consent-gated follow-up automation (Role 4)

Acts on every **Hot** lead from Agent A. It checks marketing consent through Role 5's gate, then either
records a blocked attempt or uses a local LLM to draft a follow-up email. The draft is stored as
**`pending_approval`** and is never sent. When a person approves it, the lead moves **New → Contacted**.

```
Agent A --lead_scored(Hot)--> Agent B --get_leads(bucket=Hot, actioned=false)--> MCP (Role 1) / REST
                                  |
                                  +-- check_consent(lead_id, "marketing")       Role 5, writes the audit row
                                  |     blocked/error -> log_interaction(no_action_consent_blocked, status=blocked)
                                  |     allowed       -> LLM draft -> log_interaction(drafted_followup_email,
                                  |                                                   status=pending_approval)
                                  '-- draft approved on the dashboard -> update_deal_stage(New -> Contacted)
```

## Quick start

```bash
# from the repo root
python -m venv .venv && .venv\Scripts\activate      # Windows (source .venv/bin/activate elsewhere)
pip install -r agent_b/requirements.txt
ollama pull llama3.2:3b                             # free, local LLM (https://ollama.com)

cd backend && uvicorn app.main:app --port 8000      # terminal 1: Role 2 backend
cd agent_a && python -m lead_scoring run            # terminal 2: Agent A
cd agent_b && python -m followup run                # terminal 3: Agent B
```

| Command | What it does |
|---|---|
| `python -m followup run` | act on Hot leads continuously (WebSocket wake-up + polling) |
| `python -m followup run --once` | one cycle, prints a JSON summary |
| `python -m followup draft lead.json` | draft an email for a lead record, no CRM needed (`-` = stdin) |
| `python -m followup harness` | 8 simulated Hot leads through the real agent and backend, with checks |
| `python -m followup harness --api-url http://127.0.0.1:8000` | same, against a running backend (leads appear on the dashboard) |
| `python -m followup harness --report reports/harness_report.md` | also write the Markdown + JSON evidence report |
| `python -m pytest -q` | 71 tests (run from `agent_b/`; Ollama not needed) |

Add `--llm template` to any command to run without Ollama.

### Configuration

| Option | Env var | Default |
|---|---|---|
| `--api-url` | `CRM_API_URL` | `http://127.0.0.1:8000` |
| `--mcp-url` | `MCP_SERVER_URL` | none (Streamable HTTP endpoint, e.g. `http://127.0.0.1:8001/mcp`) |
| `--mcp-command` | `MCP_SERVER_COMMAND` | none (stdio, e.g. `"python mcp_server/server.py"`) |
| `--transport` | `AGENT_B_TRANSPORT` | `auto`: MCP with REST fallback if MCP is configured, else REST |
| `--advance-stage` | `AGENT_B_ADVANCE_STAGE` | `on_approval` (also `on_draft`, `never`) |
| `--llm` | `AGENT_B_LLM` | `ollama` (template fallback); `template` = no LLM |
| `--ollama-url` / `--ollama-model` | `OLLAMA_URL` / `AGENT_B_MODEL` | `http://127.0.0.1:11434` / `llama3.2:3b` |
| `--sender-name` / `--sender-org` | `AGENT_B_SENDER_NAME` / `AGENT_B_SENDER_ORG` | `X Education Admissions Team` / `X Education` |
| `--interval` | `AGENT_B_POLL_INTERVAL` | `3` s |
| `--no-ws` | `AGENT_B_WS=0` | WebSocket wake-up on `lead_scored` (Hot) and `interaction_updated` (approved) is on |
| `--metrics-file` | `AGENT_B_METRICS_FILE` | off; JSON lines with score→action latency per lead |

## Results

**Harness** ([reports/harness_report.md](reports/harness_report.md)): 8 simulated Hot leads.
- 5 have marketing consent granted, including one with a prompt injection in the product-interest field.
- 2 have marketing consent denied.
- 1 has no consent record.
- **All 6 checks pass.**
- All 5 consented leads got LLM drafts. All 3 others were blocked, and each block references its audit-log entry.
- Approving a draft moved the lead to Contacted. Rejecting one left the lead in New.
- A second run did nothing.
- Speed: 5.5 s per lead on a laptop CPU with `llama3.2:3b`.

**Live run** (real uvicorn backend, Agent B polling only every 30 s, so the WebSocket did the waking):

| Lead | Outcome | Time after Hot score |
|---|---|---|
| Consent denied | `no_action_consent_blocked` | 0.08 s |
| Consent granted | LLM draft, `pending_approval` | 9.8 s |
| Draft approved | New → Contacted | 0.26 s after approval |

All three are well inside the report's 30 s end-to-end target.

## Integration contracts

### What Agent B writes (`log_interaction`)

The brief's deliverable `{lead_id, action_taken, action_payload, status, consent_check, acted_at}` maps to
the backend's interaction record like this:

| Brief | Interaction field | Drafted | Blocked |
|---|---|---|---|
| `lead_id` | `lead_id` | lead | lead |
| `action_taken` | `action_type` | `drafted_followup_email` | `no_action_consent_blocked` |
| `status` | `status` | `pending_approval` | `blocked` |
| `consent_check` | `consent_check` | `passed` | `blocked` (`error` if the check itself failed) |
| `action_payload` | `payload` | see below | `{purpose, reason, consent_on_file, audit_log_id, lead_bucket, lead_score, acted_at}` |
| `acted_at` | `payload.acted_at` (also `created_at`) | | |

The payload of a drafted interaction:

```json
{
  "email": {"to": "...", "subject": "...", "body": "Hi Ananya,\n\n...\n\nBest regards,\n...",
            "generator": "llm", "model": "ollama/llama3.2:3b", "attempts": 1},
  "purpose": "marketing",
  "consent_reason": "Consent granted for purpose 'marketing'",
  "audit_log_id": "…",
  "suggested_next_step": "Review and send this email, then book a 15-minute call. Approving moves the lead to Contacted.",
  "reviewer_context": {"lead_score": 0.91, "bucket": "Hot", "top_factors": ["…"], "model_version": "…"},
  "acted_at": "2026-10-27T16:33:27+00:00"
}
```

`email.generator` is `template` when the LLM was unavailable or its drafts failed the checks. In that case
`email.fallback_reason` says why. `email.dropped_fields` lists any form field left out of the email, with
the reason.

### For Role 6 (dashboard)

- **Approval queue:** `GET /interactions?status=pending_approval`. Show `payload.email.subject` and `body`,
  plus `reviewer_context.top_factors`.
- **Approve or reject a draft:** `PATCH /interactions/{id}/status` with `{"status": "approved" | "rejected"}`.
  Agent B moves an approved lead to Contacted within about a second.
- **Activity feed:** a `no_action_consent_blocked` row carries `payload.reason`, for example "Agent B tried to email
  Priya — blocked: Consent denied for purpose 'marketing'". The matching consent check is in `GET /audit-log`,
  where `log_id` equals `payload.audit_log_id`.

### For Role 1 (MCP tools Agent B calls)

| Tool | Arguments Agent B sends | Required? |
|---|---|---|
| `get_leads` | `filter={"bucket": "Hot", "actioned": false, "limit": n}`, or flat arguments that include `actioned` | yes |
| `log_interaction` | `lead_id, action_type, payload, status, consent_check` (+ `actor` if accepted) | yes |
| `check_consent` | `lead_id, purpose` (+ `actor`, `action_attempted` if accepted) | no, REST if absent |
| `get_lead_by_id` | `lead_id` | no, REST if absent |
| `update_deal_stage` | `lead_id` + one of `new_stage` / `stage` / `pipeline_stage` | no, REST if absent |
| `list_interactions` | `status, action_type, limit` | no, REST if absent |

The client reads each tool's input schema and adapts to it. A tool that can't take these arguments is
skipped and that one call goes over REST; the other MCP tools stay in use. Two of these checks protect
correctness, not just convenience:
- `get_leads` must filter on `actioned`. Otherwise Hot leads would be drafted again on every poll.
- `log_interaction` must take `status`. Otherwise a blocked attempt would be stored as `pending_approval`.

For a missing lead, raise `ToolError("Lead <id> not found")`.

## Design decisions

- **Consent is a gate, not a filter (report 5.1).** Agent B fetches every Hot lead and then asks Role 5,
  so a blocked lead is visible in the activity feed. The check runs immediately before the lead's data is
  used (FR6), so consent withdrawn after the lead was created still blocks.
- **Fail closed (5.3).** An unusable consent answer is logged as blocked, with `consent_check="error"`. If
  the CRM is unreachable, Agent B does nothing and retries later.
- **Human approval boundary (FR5).** Nothing is ever sent. By default the stage changes only after a person
  approves; it never moves backwards, and never moves a lead a person has already advanced past New.
- **Stateless (5.1).** The CRM's `actioned` flag is Agent B's only memory. A restart picks up exactly the
  Hot leads that have no interaction yet. A lead whose draft fails is retried, then set aside after 3
  attempts so it can't block newer leads.
- **Consistent drafts.** Code writes the greeting and sign-off. The model writes only 2–3 paragraphs, in a
  server-enforced JSON schema, at temperature 0.2 with a fixed seed.
- **Data minimisation.** The LLM sees only first name, occupation and course of interest. It never
  sees the email address, phone number, notes, consent records or the score. The score and
  `top_factors` go only into `reviewer_context`, never into customer-facing text.
- **Untrusted form input.** Before a draft is stored:
  - **Form fields:** a field that looks like an instruction, or that contains a link, a price or a
    placeholder, is dropped from the prompt.
  - **Generated draft:** rejected if it contains placeholders, links, contact details, prices, discounts,
    scoring details or an invented "our recent conversation". It is regenerated once, then replaced by
    the template.

  This matters in practice: in testing, `llama3.2:3b` really did offer a "90% discount" when told to in a
  form field, and did invent earlier calls.
- **Local LLM.** Ollama keeps the project free of paid cloud services and API keys (NFR Portability).
  Any Ollama model works through `--ollama-model`.

## Layout

```
agent_b/
  followup/
    agent.py            FollowUpAgent: consent gate, logging, stage policy, loop, WebSocket wake-up
    drafting.py         lead context, LLM (Ollama) + template drafters, output checks
    harness.py          simulated Hot-lead harness + report
    harness_leads.json  the 8 simulated leads
    crm/                MCP client, REST client, fallback policy
    __main__.py         CLI
  reports/              harness_report.md / .json (evidence)
  tests/                unit + integration (real backend in-process; MCP servers in-process; LLM mocked)
```
