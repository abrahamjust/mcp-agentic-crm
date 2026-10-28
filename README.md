# Agentic CRM

A local CRM with a FastAPI dashboard, Agent A lead scoring, and Agent B consent-gated follow-up drafts.

## What you need

- Windows, PowerShell, and Python 3.12.
- Internet access for the first dependency installation.
- Ollama is optional. Agent B can use its built-in template drafter without installing an LLM.

## Install dependencies

Run from the repository root in PowerShell. This installs the backend and both
agents' dependencies into the currently selected Python installation:

```powershell
python -m pip install --upgrade pip
pip install -r agent_a/requirements.txt -r agent_b/requirements.txt
```

The Agent A model is already included in `agent_a/models/`; model training or
Kaggle credentials are not required.

## Start the full app

Open **three PowerShell terminals** in the repository root and start each
component in its own terminal, in the order below. Keep all three running
while using the full app.

**Terminal 1 - CRM backend and dashboard**

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir backend
```

The backend serves the frontend dashboard and form; there is no separate
frontend server or frontend install command. Open <http://127.0.0.1:8000/>
after starting the backend.

**Terminal 2 - Agent A scorer**

```powershell
Set-Location agent_a
python -m lead_scoring run
```

Start Agent A before adding leads you want scored. Its continuous mode leaves
leads that existed before it started untouched. To score the current unscored
queue instead, run `python -m lead_scoring run --once` from `agent_a`.

**Terminal 3 - Agent B follow-up drafts**

For a no-extra-service setup, use the built-in template drafter:

```powershell
Set-Location agent_b
python -m followup run --llm template --transport rest
```

Or, to draft with the local Ollama model:

1. Install Ollama from [ollama.com](https://ollama.com).
2. In another terminal, download the model:

   ```powershell
   ollama pull llama3.2:3b
   ```

3. Ensure Ollama is running, then start Agent B without `--llm template`:

   ```powershell
   Set-Location agent_b
   python -m followup run --transport rest
   ```

Each agent command above is run from its own directory in its terminal.
Agent B connects to the backend at `http://127.0.0.1:8000`.

| Page | URL |
|---|---|
| Overview | `/` |
| Lead pipeline | `/pipeline` |
| Follow-up approvals | `/approvals` |
| Agent activity | `/activity` |
| Course enquiry form (public) | `/leads/new` |
| API documentation | `/docs` |

The course enquiry form is the page a prospective learner fills in; it would
be embedded on the education provider's website, and is hosted by the CRM for
now. Open it from **Enquiry form** in the dashboard sidebar (it opens in a new
tab) after the workers are running, and submit an enquiry: it appears on the
dashboard straight away. Agent A scores new leads; Agent B only drafts
follow-ups for Hot leads with marketing consent.
Drafts appear on the Approvals page. Opening and approving a draft opens a
prefilled email in your default mail app and records approval in the CRM, but
does not send the email. Review and send it manually from your mail app.

The SQLite database is created at `backend/crm.db` by default. An older
database is upgraded automatically at startup (new columns added, the removed
company/job-title columns dropped). To stop a
component, press `Ctrl+C` in its terminal.

## Run tests

Run each test command from its component directory:

```powershell
Set-Location backend
python -m pytest -q
Set-Location ..\agent_a
python -m pytest -q
Set-Location ..\agent_b
python -m pytest -q
```

## More details

- [Backend API and configuration](backend/README.md)
- [Agent A scoring behavior and configuration](agent_a/README.md)
- [Agent B follow-up behavior and configuration](agent_b/README.md)
