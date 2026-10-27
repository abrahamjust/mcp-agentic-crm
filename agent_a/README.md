# Agent A: ML lead scoring (Role 3)

Scores every new lead with its conversion probability, a **Hot / Warm / Cold**
bucket and the **top 3 reasons**, and writes the result back to the CRM
through the MCP tool layer (REST fallback).

```
backend (Role 2) --lead_created--> Agent A --get_leads(unscored)--> MCP (Role 1) / REST
                                       |  LeadScorer: model + explanations
                                       '--update_lead_score(score, bucket, top_factors, model_version)
```

## Results (held-out test set, 1,841 leads)

| Model | Accuracy | ROC-AUC | PR-AUC | F1 @0.5 |
|---|---|---|---|---|
| Majority-class baseline | 0.617 | 0.500 | 0.383 | 0.000 |
| Logistic regression (baseline) | 0.754 | 0.801 | 0.748 | 0.613 |
| XGBoost | 0.815 | 0.873 | 0.821 | 0.743 |
| **Random forest, calibrated (selected)** | **0.814** | **0.868** | **0.815** | **0.738** |

- ROC-AUC 95% bootstrap CI: 0.852 – 0.884; gain over logistic regression: +0.051 – +0.081.
- Buckets on test: **Hot** 32% of leads, 80% convert; **Warm** 44%, 26%; **Cold** 24%, 6%
  (Cold holds only 3.8% of all conversions).

Full numbers, calibration, buckets and figures:
[reports/model_report.md](reports/model_report.md). Data exploration and the
leakage audit: [reports/eda.md](reports/eda.md).

Live latency (real uvicorn backend, WebSocket wake-up): lead submitted →
scored in **0.25 s median, 0.57 s max** over 8 demo leads.

## Quick start

```bash
# from the repo root
python -m venv .venv && .venv\Scripts\activate      # Windows (source .venv/bin/activate elsewhere)
pip install -r agent_a/requirements.txt

cd backend && uvicorn app.main:app --port 8000      # terminal 1: Role 2 backend
cd agent_a && python -m lead_scoring run            # terminal 2: Agent A
```

A trained model is committed in `models/`, so no training or Kaggle account
is needed to run the agent.

| Command | What it does |
|---|---|
| `python -m lead_scoring run` | score new leads continuously |
| `python -m lead_scoring run --once` | one cycle, prints a JSON summary |
| `python -m lead_scoring score lead.json` | score a lead record from a file (`-` = stdin) |
| `python -m lead_scoring info` | show the loaded model, thresholds, metrics |
| `uvicorn lead_scoring.service:app --port 8002` | optional HTTP scorer: `POST /score`, `GET /model` |
| `python -m lead_scoring.training.download` | fetch the dataset (needs `~/.kaggle/kaggle.json`) |
| `python -m lead_scoring.training.eda` | regenerate `reports/eda.md` |
| `python -m lead_scoring.training.train` | retrain (≈4 min), writes `models/<version>/` and `reports/` |
| `python -m pytest -q` | tests (run from `agent_a/`) |

### Configuration

| Option | Env var | Default |
|---|---|---|
| `--api-url` | `CRM_API_URL` | `http://127.0.0.1:8000` |
| `--mcp-url` | `MCP_SERVER_URL` | none (Streamable HTTP endpoint, e.g. `http://127.0.0.1:8001/mcp`) |
| `--mcp-command` | `MCP_SERVER_COMMAND` | none (stdio, e.g. `"python mcp_server/server.py"`) |
| `--transport` | `AGENT_A_TRANSPORT` | `auto`: MCP with REST fallback if MCP is configured, else REST |
| `--interval` | `AGENT_A_POLL_INTERVAL` | `2` s |
| `--batch-size` | `AGENT_A_BATCH_SIZE` | `50` |
| `--no-ws` | `AGENT_A_WS=0` | WebSocket wake-up on `lead_created` is on |
| `--metrics-file` | `AGENT_A_METRICS_FILE` | off; JSON lines with creation→score latency per lead |
| `--model-dir` | `AGENT_A_MODEL_DIR` | `models/<LATEST>` |

The continuous `run` worker leaves leads that were already in the CRM when it
started untouched. It scores newly submitted leads; start it before submitting
the leads you want scored. The explicit `run --once` command still scores the
current unscored queue.

## Integration contracts

### Inputs (Role 2 backend, Role 6 form)

The model uses these **optional** lead fields (added to the backend in a
separate commit for Role 2 to review). A blank field never blocks scoring.

| Field | Form values | Notes |
|---|---|---|
| `lead_origin` | Landing Page, API, Lead Add Form, Lead Import | how the lead entered the CRM |
| `source` | Website/Referral/Social/... or Google, Organic Search, Reference, ... | existing field |
| `occupation` | Working Professional, Business Owner, Student, Unemployed, Homemaker, Other | if blank but the job title gives a seniority, treated as Working Professional |
| `total_visits`, `time_on_website_sec`, `pages_per_visit` | numbers ≥ 0 | web-analytics engagement |

Consent is **not** a model input: scoring stays consent-agnostic and Role 5's
gate alone decides what may be done with a lead.

### MCP tools Agent A calls (Role 1)

| Tool | Arguments | Returns |
|---|---|---|
| `get_leads` | `filter={"unscored": true, "limit": n}` **or** flat `unscored=true, limit=n` | list of lead objects (`GET /leads` JSON) |
| `update_lead_score` | `lead_id, score, bucket, top_factors` (+ `model_version` if accepted) | the updated lead |

The client reads each tool's input schema and adapts to either argument
style. Please raise `ToolError("Lead <id> not found")` (from
`mcp.server.mcpserver.exceptions`) for a 404 so the message reaches the
client; a plain exception is reported only as "Error executing tool". If
either tool is missing or the server is down, Agent A logs a warning, uses
REST, and retries MCP every 30 s.

### Output (Role 4 Agent B, Role 6 dashboard)

`PATCH /leads/{id}/score` with
`{"score": 0.839, "bucket": "Hot", "top_factors": ["Occupation: Working Professional (raises score)", ...], "model_version": "random_forest-20261027-2a29d5ae"}`.
The backend then broadcasts `lead_scored`. Agent B can act on `bucket == "Hot"`.

## Design decisions

- **No target leakage.** Only fields known when a lead is created are inputs.
  `Tags`, `Lead Quality`, `Lead Profile`, `Last (Notable) Activity` and the
  `Asymmetrique` scores are recorded by sales staff after contact (some
  convert at 97–99%) and are excluded; reasons are in `dataset.py` and the report.
- **One canonical mapping.** Training rows go through the backend's own
  `app/standardize.py` normalizers, so `"Google"` means `Website` in both
  training and live scoring (FR2).
- **Honest evaluation.** 60/20/20 stratified split; models compared by 5-fold
  CV on train only; the simplest model within 0.005 AUC of the best is chosen
  (random forest 0.872 vs XGBoost 0.873); Platt calibration and bucket cut-offs
  fitted on validation; the test set is used once.
- **Thresholds from data.** Hot = lowest score with ≥80% precision on
  validation; Cold = at most 5% of converters below it.
- **Explanations cite evidence only.** Per-lead TreeSHAP contributions summed
  per input field; imputed values (missing numbers, blank origin/source) are
  never shown as a reason.
- **Stateless, fail-safe agent.** The CRM is the only state: a lead stays
  `Unscored` until its write succeeds, so restarts and outages self-heal.
  Writes are idempotent; one bad lead is retried 3× then set aside without
  blocking others; exponential backoff while the CRM is down.
- **Artifact integrity.** `model.joblib` is a pickle; it is loaded only if its
  sha256 matches `model_card.json`.

## Layout

```
agent_a/
  lead_scoring/
    features.py      input contract shared by training and serving
    dataset.py       Kaggle -> CRM fields (via backend normalizers), leakage policy
    thresholds.py    Hot/Warm/Cold cut-offs from validation data
    explain.py       per-lead contributions grouped by input field
    predictor.py     LeadScorer
    artifacts.py     versioned model save/load with sha256 check
    crm/             MCP client, REST client, fallback policy
    agent.py         polling agent + WebSocket wake-up
    service.py       optional FastAPI scorer
    __main__.py      CLI
    training/        download, eda, train, metrics, plots
  models/            committed model + model card (LATEST points at it)
  reports/           eda.md, model_report.md, metrics.json, figures/
  tests/             unit + integration (real backend in-process, MCP in-process and over stdio)
```
