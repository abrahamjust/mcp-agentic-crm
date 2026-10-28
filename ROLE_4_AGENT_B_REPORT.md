# Role 4: Automation Agent (Agent B)

**Name:** Venkataraman R
**Registration number:** 23MIA1025
**Role:** Automation Agent (Agent B)
**Verification date:** 27 October 2026

## 1. Scope

This work implements only the Role 4 functionality, in a new `agent_b/` module. It runs on top of three
existing modules:
- the Core CRM Backend (Role 2)
- Agent A's scoring output (Role 3)
- the consent check and audit log (Role 5)

No backend, Agent A or consent code was changed. Agent B uses only the documented REST endpoints, and the
MCP tool contract once Role 1's server is available. Until then it falls back to REST, as design document
section 5.3 allows.

## 2. Deliverables completed

| Requirement (project report 5.5, Role 4) | Implementation | Status |
|---|---|---|
| Monitor Hot leads that have not yet received an automated action | Polls `get_leads(bucket=Hot, actioned=false)`. A WebSocket listener on `lead_scored` (Hot) wakes it immediately. On restart it catches up on every Hot lead with no interaction yet. | Complete |
| Call `check_consent(lead_id, "marketing")` before any customer-facing action | Called for every Hot lead immediately before its data is used, with `actor=agent_b`, `action_attempted=drafted_followup_email`. Role 5 writes the audit entry. | Complete |
| If consent is denied, record `no_action_consent_blocked` and stop | Logged with `status=blocked`, `consent_check=blocked`, the reason and the audit-log id. The LLM is never called for that lead. An unusable consent answer is also logged as blocked (fail closed, report 5.3). | Complete |
| When consent is granted, use an LLM to write a concise, consistent follow-up email from the lead's name, occupation and course of interest | Local LLM (Ollama, `llama3.2:3b`), with a JSON schema, low temperature and a fixed seed. Code adds the greeting and sign-off. The output is checked, regenerated once if it fails, and replaced by a template if it still fails. | Complete |
| Store the message as `pending_approval`, not sent | Logged as `drafted_followup_email`, `status=pending_approval`, `consent_check=passed`. Agent B has no way to send email. | Complete |
| Optionally advance the pipeline stage (New → Contacted) with `update_deal_stage` | Done by default when a person approves the draft (`--advance-stage on_approval`). Also offered: `on_draft` and `never`. A lead never moves backwards. | Complete |
| Test harness with 5–10 simulated Hot leads covering consent granted and denied | `python -m followup harness` runs 8 leads: 5 granted, 2 denied, 1 with no record. It checks each outcome, the audit link, the approval boundary and idempotency. | Complete |

The deliverable record `{lead_id, action_taken, action_payload, status, consent_check, acted_at}` is stored
through `log_interaction`. [agent_b/README.md](agent_b/README.md#what-agent-b-writes-log_interaction) shows
how it maps onto the interaction fields.

## 3. Requirement traceability

| Requirement | How Agent B meets it | Evidence |
|---|---|---|
| FR5 Bounded automation | Drafts only after the consent gate. Nothing is sent. The stage changes only after human approval. | `test_agent.py` approval tests; harness checks 3–5 |
| FR6 Purpose-specific consent | Checks `marketing` at the point of action. Consent withdrawn after creation still blocks. | `test_consent_is_checked_at_the_point_of_action` |
| FR7 Auditability | Every Hot lead gets one interaction plus one audit row, linked by `audit_log_id`. | Harness check 1; `test_*_blocked_and_recorded` |
| FR8 Interoperability | MCP client adapts to the tool schemas, falls back to REST per call or on outage. | `test_crm_clients.py` (9 MCP cases) |
| NFR Reliability | A blocked or failed action is never silent. CRM outages cause retries, not lost leads. | `test_unreachable_crm_does_nothing_and_retries_later` |
| NFR Compliance-by-design | Automated allow and block cases; the LLM sees only the minimum fields. | `test_prompt_contains_no_contact_details_consent_or_score` |
| NFR Portability | Free local LLM; template mode needs no LLM at all. | `--llm template` |

## 4. LLM safety findings

Probing `llama3.2:3b` while building the drafter turned up three problems, each now handled:
1. **Placeholders.** It wrote "[insert day of the week]". Rejected by the output check.
2. **Prompt injection.** It obeyed an instruction typed into a form field and offered a "90% discount".
   Such fields are now dropped before the prompt is built, and prices and discounts are rejected in the
   output.
3. **Invented contact.** It opened with "our recent conversation", although this is the first contact.
   The prompt now says so explicitly, and the output check rejects it.

In the final harness run, all 5 consented leads received LLM drafts that passed every check. The injected
field was dropped and recorded in `dropped_fields`.

## 5. Files added

- [`agent_b/followup/agent.py`](agent_b/followup/agent.py): consent gate, interaction logging, stage policy,
  polling loop and WebSocket wake-up.
- [`agent_b/followup/drafting.py`](agent_b/followup/drafting.py): lead context, Ollama and template
  drafters, output checks.
- [`agent_b/followup/crm/`](agent_b/followup/crm/): MCP client, REST client, fallback policy.
- [`agent_b/followup/harness.py`](agent_b/followup/harness.py) and
  [`harness_leads.json`](agent_b/followup/harness_leads.json): the simulated Hot-lead harness.
- [`agent_b/tests/`](agent_b/tests/): 71 tests.
- [`agent_b/reports/harness_report.md`](agent_b/reports/harness_report.md): harness evidence, including
  every draft.
- [`agent_b/README.md`](agent_b/README.md): usage, configuration, and the contracts for Roles 1 and 6.

## 6. Verification

Commands executed:

```bash
cd agent_b
python -m pytest -q
python -m followup harness --report reports/harness_report.md
cd ../backend
python -m pytest -q
```

Results:
- **Agent B tests:** 71 passed. They use the real backend in-process, in-process MCP servers and a mocked
  LLM.
- **Harness with the LLM:** 8 of 8 leads and 6 of 6 checks passed, at 5.5 s per lead on CPU.
- **Backend tests:** 65 passed. They are unaffected because no backend code was changed.
- **Live run** (uvicorn backend plus `python -m followup run`):
  - blocked lead logged 0.08 s after its Hot score
  - LLM draft stored 9.8 s after the score
  - approved lead moved to Contacted 0.26 s after approval

## 7. Integration status and remaining work

- **Role 1 (MCP):** Agent B is tested against MCP servers that follow the tool contract. It will switch to
  Role 1's server when `MCP_SERVER_URL` or `MCP_SERVER_COMMAND` is set. Agent B needs `get_leads` to filter
  on `actioned`, and `log_interaction` to accept `status` and `consent_check`. The README lists the full
  tool contract.
- **Role 6 (dashboard):** the approval queue and the approve/reject call are documented in the README.
  Until the dashboard exists, drafts can be approved from the backend's `/docs` page.
- **Not tested:** a fully live run with the real Agent A process. Its pinned dependencies need a newer
  Python than the 3.10 on this machine, so the harness and the live run simulated Agent A's write-back
  through `PATCH /leads/{id}/score`, which is the same contract.

## 8. Final assessment

The implementation meets every Role 4 deliverable in the project report. It is limited to the automation
agent. Consent decisions fail closed, every Hot lead leaves a visible record, and customer-facing
communication stays under human approval.
