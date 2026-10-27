# MCP tool layer

This server exposes the CRM backend's REST operations as MCP tools. It does
not connect to SQLite or reimplement CRM or consent rules. Start the backend
first; the backend remains the single component that writes to the database.

## Run on Windows

From the repository root, install the small MCP server dependency set and
start the server:

```powershell
python -m pip install -r mcp_server/requirements.txt
$env:CRM_API_URL = "http://127.0.0.1:8000"  # optional; this is the default
python -m mcp_server.server
```

The server uses MCP stdio transport. Configure the MCP client to launch
`python -m mcp_server.server` with the repository root as its working
directory. Agent A is normally started from `agent_a/`, so set its command
to `python ..\mcp_server\server.py`; its client passes the process environment
through, including `CRM_API_URL`.

Start the CRM API separately as described in [`../backend/README.md`](../backend/README.md):

```powershell
Set-Location backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

## Tools

| Tool | Backend route |
|---|---|
| `get_leads` | `GET /leads` (`bucket`, `stage`, `unscored`, `actioned`, `limit`, `offset`) |
| `get_lead_by_id` | `GET /leads/{lead_id}` |
| `update_lead_score` | `PATCH /leads/{lead_id}/score` |
| `update_deal_stage` | `PATCH /leads/{lead_id}/stage` |
| `log_interaction` | `POST /leads/{lead_id}/interactions` |
| `check_consent` | `POST /consent/check` |

The backend validates inputs, returns its normal JSON response, broadcasts
its existing WebSocket events, and persists consent-check audit records.
HTTP validation and not-found errors are returned to MCP clients as tool
errors. Set `CRM_API_URL` in the MCP server process if the API is hosted at a
different address.
