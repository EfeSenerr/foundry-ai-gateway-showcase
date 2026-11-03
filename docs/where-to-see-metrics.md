# Where to see metrics (PortOps AI Gateway PoC)

Resources (RG `rg-portops-poc-<suffix>`; `<suffix>` is the 5-character suffix chosen at deploy time): APIM `apim-portops-<suffix>`, App Insights `appi-portops-<suffix>`, Log Analytics `log-portops-<suffix>`, Foundry `aif-portops-<suffix>` (projects `ops`, `it`, `cs`), budget `budget-portops-<suffix>`, MCP Container App `ca-portops-mcp`.
Departments = APIM products / subscriptions `dept-ops`, `dept-it`, `dept-cs`.

## Cheat sheet

| What you want to see | Where (portal path, exact clicks) | What it proves | Data latency |
|---|---|---|---|
| **Everything on one page** (tokens, est. cost, 429/5xx, latency, request lookup) | portal.azure.com → **Monitor** → **Workbooks** → filter RG `rg-portops-poc-<suffix>` → **PortOps AI Gateway – Cost & Operations**. Direct link: printed by `scripts\deploy-workbook.ps1`. Set **Time range** (top) and **Department** (multi-select) | Per-department attribution, throttling, gateway-vs-backend errors, latency, all from the gateway | 2–5 min |
| **Tokens per department, live chart** | portal → resource group → **appi-portops-<suffix>** → left menu **Monitoring → Metrics** → **Metric Namespace** = `portops` (listed as a *custom* / `azure.applicationinsights` namespace) → **Metric** = `Total Tokens` (also `Prompt Tokens`, `Completion Tokens`) → **Aggregation** = `Sum` → **Apply splitting** → Values = `Department` → set time to last 30 min / 4 h → **Pin to dashboard** (top right) | The `llm-emit-token-metric` policy emits tokens tagged with Department/Model/Subscription ID/API ID. Verified metric names in namespace `portops`: Total Tokens, Prompt Tokens, Completion Tokens, Prompt Cached/Audio Tokens, Completion Reasoning/Audio/Accepted/Rejected Prediction Tokens, Cache Creation Tokens, `azure.ai_gateway.client.token.usage` | 1–3 min |
| **Ad-hoc KQL** (the verified queries) | portal → **log-portops-<suffix>** → **Logs** → close the query picker → paste a block from `observability/queries.kql` (tables `AppMetrics`, `ApiManagementGatewayLogs`, `ApiManagementGatewayLlmLog`, `AzureDiagnostics`). From a terminal: `python observability/run_queries.py --only tokens_per_dept` | Raw proof behind every tile; lookup by `apim-request-id` (= `CorrelationId`) | 2–5 min (first LLM logs can take ~10) |
| **Built-in gateway dashboards** (requests, latency, errors by API/product/subscription) | portal → **apim-portops-<suffix>** → **Monitoring → Analytics** → tabs *Timeline / Geography / APIs / Operations / Products / Subscriptions / Users*; time range top-left | Per-product (department) volume and failure rate without any KQL | ~1–5 min (Analytics can lag up to ~15 min) |
| **Who is allowed what** (department keys, quotas) | **apim-portops-<suffix>** → **APIs → Products** → `dept-ops` → *Settings / APIs / Policies*; **Subscriptions** → key per department | Each department has its own product + key; the token limit (`llm-token-limit`) is on the product/API policy | immediate (config) |
| **Foundry usage, traces, agents** | **ai.azure.com** → sign in → select project (`ops`, `it` or `cs`) → left menu **Operate** (or **Monitor**) → *Overview / Admin / Quota*, and for agents **Build → Agents → pick agent → Traces / Monitor**. Account side: portal → **aif-portops-<suffix>** → **Monitoring → Metrics** (Azure OpenAI requests, processed tokens) | What Foundry itself served (backend view), agent runs and tool calls. Note: calls via the gateway are not attributed to a project here – use the workbook for per-department | metrics 1–3 min, traces ~1–5 min |
| **Foundry diagnostics** | Workbook section 7, or `log-portops-<suffix>` → Logs → `AzureDiagnostics \| where Category == "RequestResponse"` (tokens in `properties_s`) | Backend status codes and latency vs. the gateway's view | 2–5 min |
| **Actual cost by department** | portal → **Cost Management** → **Cost analysis** → scope = resource group `rg-portops-poc-<suffix>` → **Group by → Tag → `project`** (then switch to *Resource* or *Meter* → *Service name/Meter*) | Real bill, chargeback by `project` tag. Token tiles in the workbook are an estimate only | **8–24 h** (not live – show the workbook, and pre-open yesterday's view) |
| **Budget and alerts** | **Cost Management → Budgets** → `budget-portops-<suffix>` (scope RG or subscription) | Spend vs. budget and alert thresholds | 8–24 h |
| **MCP server logs** | portal → Container App **ca-portops-mcp** → **Monitoring → Log stream** (live, Console tab) · or **Monitoring → Logs** → `ContainerAppConsoleLogs_CL \| where ContainerAppName_s == "ca-portops-mcp" \| order by TimeGenerated desc` | The tool calls the agent actually made | stream: seconds; Logs: 2–5 min |

## Phase 2 visibility: per user, per app, MCP, blocked requests, budget

Workbook sections 8–12 (same workbook, scroll down). Dimensions come from the `AppMetrics` token metrics (`Team`, `User`, `ClientApp`, `Model`) and the `McpToolCalls` metric (`Team`, `Tool`, `Result`).

| Section | Shows | Notes |
|---|---|---|
| 8 Per user and team | tokens + est. USD per team, top users, tokens over time per user | Only `/entra/openai` calls have a `User`; key-based calls appear as `(department key - no user)` |
| 9 Per calling app/agent | tokens + est. USD per `ClientApp` | Entra app id (`azp`); for key calls falls back to the APIM subscription id |
| 10 MCP tool calls | calls per team/tool, allowed vs blocked (table, bar, over time), blocked list | Tool-level blocks are JSON-RPC errors (HTTP 200) so they only show here, not in section 11 |
| 11 Blocked requests by reason | 401/403/429 from `ApiManagementGatewayLogs`, classified | Heuristics: `openai-entra` 401 = no/invalid token (`TokenNotPresent`); 403 + `LastErrorReason=OpenAITokenQuotaExceeded` = quota exhausted; 403 on `openai` with empty `LastErrorReason` = `TeamBudgetExhausted` kill switch; `mcp-*` 403 + header `x-portops-mcp-result=wrong-team` = wrong team MCP path; `mcp-*` 401 = bad MCP key; 429 = `OpenAITokenLimitExceeded` (TPM) |
| 12 Budget enforcement | est. USD today per team vs spend-alert thresholds (ops 5 / it 1 / cs 2), 403 kill-switch rejections, enforcer Logic App runs | Logic App runs need the workflow diagnostic setting (`WorkflowRuntime` → Log Analytics); until deployed the table is empty – use the Logic App **Runs history** or the UI page below |

Threshold values in section 12 are hard-coded in `infra/workbook/build_workbook.py` (`thr` datatable) – keep them in sync with the spend alerts.

## UI: Governance page (`ui/pages/2_Governance.py`)

`scripts\run-ui.ps1` → http://localhost:8501 → **Governance** (left menu). Tabs:

- **My limits (Entra)** – calls `/entra/openai` with your `az login` token; shows team, remaining team quota, remaining user tokens/quota.
- **Team kill switch** – shows `team-<x>-enabled` and estimated USD today; **Simulate budget alert** (Logic App → 403 `TeamBudgetExhausted`), **Reset** (re-enable via the reset Logic App), **Test call** (200 vs 403).
- **MCP governance** – runs scenarios a–d (allowed tool, tool blocked for team, wrong-team path 403, direct call 401) with a results table.
- **Where to see it** – portal links (Workbook, App Insights metrics, APIM, named values, Logic App run history, Cost analysis).

## Pin the Metrics Explorer chart to a dashboard

1. App Insights `appi-portops-<suffix>` → **Metrics**.
2. Namespace `portops`, metric `Total Tokens`, aggregation **Sum**.
3. **Apply splitting** → `Department`. Chart type **Bar** or **Line**. Time range: *Last 4 hours*.
4. Add a second chart (**Add metric**): `Prompt Tokens` / `Completion Tokens` if wanted.
5. **Save to dashboard → Pin to dashboard** → create new, e.g. “PortOps live”, type *Shared*.

If **Department** is missing in *Apply splitting*: check App Insights `CustomMetricsOptedInType` = `WithDimensions` (set by `infra/modules/monitoring.bicep`; portal: **Usage and estimated costs → Custom metrics** → *With dimensions*). Only data sent after enabling it has dimensions. Fallback is always the workbook (reads the same data from `AppMetrics`).

## What to show if a view is empty

- **Metrics / workbook tiles empty**: widen **Time range**, then generate traffic: `demo\.venv\Scripts\python.exe demo\run_all_smoke.py --skip-agents` (TPM limit 2000 – wait 60 s between bursts) and wait 2–5 min. Meanwhile show APIM **Analytics** or the Container App **Log stream**, which are faster.
- **LLM log / lookup section empty**: needs diagnostic categories `GatewayLogs` + `GatewayLlmLogs` with LLM logging enabled on the API diagnostic; use the gateway row and `AppMetrics` instead.
- **Cost analysis empty or flat**: cost data lags 8–24 h. Show the workbook estimate (“illustrative list price”), the budget definition, and explain `project`-tag chargeback.
- **Foundry Operate/Monitor empty**: traffic through the gateway is not tied to a project; use the Foundry account metrics or workbook section 7.

## Deploy / refresh the workbook

```powershell
.\scripts\deploy-workbook.ps1          # reads .env, deploys infra/workbook/workbook.bicep, prints the link
demo\.venv\Scripts\python.exe infra\workbook\build_workbook.py --validate   # regenerate JSON + run every KQL against the workspace
```
