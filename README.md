# Foundry + AI Gateway PoC: Terminal Ops Assistant

Workshop PoC: many departments, one platform. A shared Foundry resource (one project per department), an Azure API Management AI Gateway (per-department key, token limits, model routing, token metrics), Log Analytics/App Insights for observability and an MCP server with synthetic data for the fictional *Northport Container Terminal*. No real data, no secrets in the repo.

## Prerequisites
- Azure subscription (Contributor + User Access Administrator), `az` logged in, PowerShell 7+.
- Python 3.11+ (demo venv: `demo\.venv`).
- Region `swedencentral`; models `gpt-4.1-mini` and `gpt-4.1`.

## Deploy
```powershell
.\scripts\setup-entra.ps1     # Entra app, budget groups, synthetic test users (identity-based limits); writes ENTRA_* to .env
.\scripts\deploy.ps1          # Foundry, APIM, Log Analytics, budget...; writes .env (git-ignored)
.\scripts\deploy-mcp.ps1      # builds + deploys the MCP server, protects it with a backend key, exposes it through APIM (MCP_GATEWAY_URL, MCP_KEY_*) + Foundry mcp-gateway-<dept> connections
.\scripts\setup-foundry-gateway.ps1   # per-project APIM connections (agents + Playground), dept key via listSecrets
cd demo
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt
python create_foundry_agents.py --test    # one prompt agent per department; model via APIM (--direct to bypass)
python run_all_smoke.py                    # PASS/FAIL end-to-end check
```

Optional: **Open WebUI with Entra SSO -> AI Gateway**. The user's own Entra token reaches APIM, which applies per-team/per-user limits and reports per user and app, with no proxy and no shared keys. Architecture options and guidance: [docs/openwebui-entra-gateway.md](docs/openwebui-entra-gateway.md).
```powershell
.\scripts\setup-apim-entra-v1.ps1     # OpenAI v1 API entra/v1 on the existing APIM + named value quota-owui-user (additive)
.\scripts\setup-openwebui-entra.ps1   # app reg portops-openwebui (SSO + Gateway.Use), secret -> openwebui\.env (git-ignored)
docker compose -f openwebui\docker-compose.yml up -d   # http://localhost:3000
```

## Demo commands (run from `demo\`)
| Demo | Command |
|---|---|
| Agent via gateway, per-dept tools | `python agent_via_gateway.py --dept ops "Which vessels arrive today?"` |
| Foundry prompt agent | `python invoke_agent.py --dept it "Summarize recent errors"` |
| Token limit -> 429 | `python token_limit_demo.py` |
| Chaos 500 + log lookup | `python chaos_500_demo.py` |
| Model routing | `python model_routing_demo.py` |
| EUR budget -> hard stop (Logic App kill switch) | `python budget_demo.py --team cs --mode simulate` (also `--mode status` / `reset`; see [docs/budgets-and-limits.md](docs/budgets-and-limits.md)) |
| Identity-based limits (Entra) | `python entra_limits_demo.py --user me --burst 5` (users: `ops`/`it`/`cs`/`me`; see [docs/budgets-and-limits.md](docs/budgets-and-limits.md)) |
| MCP governance through the gateway (team keys, tool allow-list, 403, backend key) | `python mcp_governance_demo.py` (see [docs/mcp-governance.md](docs/mcp-governance.md)) |
| Open WebUI (Entra SSO) -> per-user quota -> 403 in the chat | `..\scripts\setup-apim-entra-v1.ps1 -UserQuota 100`, chat twice at http://localhost:3000; restore with `-UserQuota 200000` (see [docs/openwebui-entra-gateway.md](docs/openwebui-entra-gateway.md)) |
| Observability | `python ..\observability\run_queries.py` (or paste `observability\queries.kql` in Log Analytics) |

Where every metric/visualization lives: [docs/where-to-see-metrics.md](docs/where-to-see-metrics.md). Further reading: [docs/resources.md](docs/resources.md).

## Teardown
```powershell
.\scripts\teardown.ps1        # deletes the resource group (and purges the Foundry account if the script does so)
```

## Repo layout
```
README.md  .env.sample  .gitignore
infra/          Bicep, APIM policies, pricing/ (shared model price table)
scripts/        setup-entra.ps1, deploy.ps1, deploy-mcp.ps1, setup-foundry-gateway.ps1, refresh-prices.ps1, setup-apim-entra-v1.ps1, setup-openwebui-entra.ps1, teardown.ps1
mcp/             FastMCP server (/mcp, /ops/mcp, /it/mcp, /cs/mcp; reachable only with the APIM-held x-mcp-key), synthetic data, tests
demo/           CLI agent, Foundry agents, 429/500/routing demos, smoke test
openwebui/      docker-compose for Open WebUI (Entra SSO, user token -> APIM entra/v1)
observability/  queries.kql, run_queries.py
docs/           architecture, budgets-and-limits, mcp-governance, openwebui-entra-gateway, cost-management, where-to-see-metrics, litellm-vs-apim, litellm-azure-integration, vnet-target, resources
```

## Workshop UI (Streamlit)
```powershell
.\scripts\run-ui.ps1        # opens http://localhost:8501
```
Page **Use cases**: department-scoped chat (AI Gateway agent or Foundry Agent), tool calls, gateway panel
(routed model, tokens, remaining quota, estimated cost, `apim-request-id`), scope test, and gateway controls
(token-limit burst -> 429, simulated backend 500 + KQL, model routing). Needs `az login` for Foundry Agent mode.
Page **Cost & usage**: estimated spend in $ per team, person and model, priced per token type (input, cached, cache
write, output) from an editable price table (`infra/pricing/model_prices.json` - the same table the budget kill switch
uses; weekly refresh: `scripts\refresh-prices.ps1 -Register`), plus estimate vs. actual from Cost Management.
Details: [docs/cost-management.md](docs/cost-management.md).
