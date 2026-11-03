# Terminal Ops Assistant MCP server

Synthetic data for a fictional container terminal (Northport Container Terminal, NCT-A / NCT-B). Demo only.

| Endpoint | Tools |
|---|---|
| `/mcp` | all 11 |
| `/ops/mcp` | `get_vessel_schedule`, `get_berth_plan`, `get_crane_assignments` + container tools |
| `/it/mcp` | `search_incident_logs`, `get_incident`, `summarize_errors` |
| `/cs/mcp` | `track_container`, `get_yard_position`, `check_customs_hold`, `list_truck_slots`, `book_truck_slot` |
| `/health` | liveness |

Streamable HTTP, stateless. Optional auth: set `MCP_API_KEY` and send header `x-mcp-key`.
Data in `data/*.json` stores time offsets; they are resolved against the current time on each call.
Regenerate with `python gen_data.py`. Department tool sets: `departments.py`.

```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest tests
.venv\Scripts\python server.py                                  # http://localhost:8000/mcp
.venv\Scripts\python tests\smoke_client.py http://localhost:8000
```

Deploy to Azure Container Apps: `scripts\deploy-mcp.ps1` (writes `MCP_URL` to `.env`).
