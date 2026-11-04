# Cost management - three levels

Facts from MS Learn "Plan and manage costs" (checked 2026-10-21); prices: `infra/pricing/model_prices.json` (list prices, verify).

| Level | Tools | Strength | Limit |
|---|---|---|---|
| 1. Azure-native | Cost analysis, `project` tag, budgets | Transparency, no code | Alerts only, no enforcement |
| 2. Gateway | APIM `llm-token-limit`, quotas, routing | Hard limits per team, real-time | Needs routing all traffic via the gateway |
| 3. Operations | FinOps exports, workbook, KQL | History, chargeback, trends | Reactive |

## Level 1 - Azure-native
- **Project chargeback (preview):** each Foundry project tags usage with `project`; filter/group Cost analysis by this tag to split a shared Foundry resource per department. Only Models sold by Azure (incl. Azure OpenAI), not Marketplace models.
- **By deployment:** Cost analysis -> costs by resource shows cost split across model deployments; deployment tags work as filters.
- **Foundry portal:** estimated agent cost (Operate -> Overview), per-agent (Assets -> Agents), per-model (Models -> Monitor).
- **Budgets:** the PoC has a monthly RG budget with 50/80/100% alerts and an action group. *Budgets only alert - there is no hard spending limit in Azure OpenAI.* Enforcement needs action groups + automation, or a gateway quota.
- Roles: Cost Management Reader; Foundry User. HTTP status alone does not decide billing - reconcile with meters.
- Open: can budgets filter on the auto-applied `project` tag?

## Level 2 - Gateway (hard limits)
- One APIM product + key per department; `llm-token-limit` (tokens-per-minute, optional quota) returns 429 + `Retry-After` when exceeded. In the PoC `it` has the lowest TPM (`demo/token_limit_demo.py`).
- `llm-emit-token-metric` emits tokens with dimensions Department, Subscription ID, API ID, Model -> per-team tokens in Azure Monitor.
- Model routing: small model by default, large model for analysis prompts (`demo/model_routing_demo.py`).
- Backend pool + circuit breaker for resilience across deployments/regions.
- LiteLLM virtual keys/budgets offer similar per-user limits; the two can be chained.

Price effect (Data Zone list price per 1M tokens, input/output): gpt-4.1-mini 0.44 / 1.76 vs gpt-4.1 2.20 / 8.80 - routing simple questions to the small model is ~5x cheaper.

## Level 3 - Operations
- **Cost exports** to storage (daily) -> Power BI / FinOps hub for chargeback.
- **KQL** in `observability/queries.kql`: `tokens_per_dept`, `cost_per_dept` (tokens per type x price table), `throttled_429`.
- **Workbook** (optional) over the same queries: tokens, estimated cost, status codes per department.
- Reconcile estimated (tokens x price) with invoiced (Cost analysis) monthly.

## Cost per team and person in dollars (UI page "Cost & usage")
Tokens alone are misleading: each model has its own price, and each request has up to four token types.

| Token type | Gateway metric (`portops` namespace) | Price (example, Data Zone list price per 1M, gpt-4.1-mini / gpt-6-astra) |
|---|---|---|
| Input (not cached) | `Prompt Tokens` − `Prompt Cached Tokens` − `Cache Creation Tokens` | 0.44 / 12.00 |
| Cached input | `Prompt Cached Tokens` | 0.11 / 1.20 |
| Cache write | `Cache Creation Tokens` | – / 15.00 |
| Output (incl. reasoning) | `Completion Tokens` | 1.76 / 60.00 |

`demo/cost.py` reads these metrics per day, team, person (`User`, only on the Entra API), calling app and model, and
multiplies each type by the model's price.

### One price table for reporting and enforcement
`infra/pricing/model_prices.json` is the **single source** for prices. `demo/cost.py` generates
`infra/pricing/price-table.kql` from it (a `prices` datatable + an `add_cost()` KQL function), which is used by:

| Consumer | How it gets the prices |
|---|---|
| UI page *Cost & usage* | reads the JSON directly |
| Spend alerts `alert-portops-spend-<team>` (budget kill switch) | `budget-enforcer.bicep` embeds `price-table.kql` + `team-spend-alert.kql` at deploy time |
| Workbook | `build_workbook.py` embeds `price-table.kql` |
| `observability/queries.kql` `cost_per_dept`, `budget_demo.py --mode status` | prepend `price-table.kql` |

When prices change (UI **Save & apply**, or `scripts/refresh-prices.ps1`), `cost.sync_azure()` rewrites the deployed
alert queries and the workbook's price block in place - no redeploy. Commit the JSON and KQL afterwards.

**Missing prices are never free.** A model or token type without a price (empty in the table) is shown as
*unpriced tokens* and the UI marks the estimate as incomplete. The spend alerts price unknown tokens at the
**highest known price** of that token type, so an unlisted model cannot slip under a team budget.

**Pricing variants.** Each model row has a *deployment type* (Data Zone, Global, Regional), *plan* (Standard =
pay-as-you-go, Flex, Priority) and *context* (Short, Long) used to pick the meter from the public
[Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
(e.g. `gpt 4.1 mini cached Inp DZone Tokens`, `6-astra ShortCo Opt Std DZ 1M Tokens`). Models whose price depends on
context length are priced at one context tier; the gateway metrics don't carry the request's context length, so long-context
requests on such models are underestimated (visible as a gap in *Estimate vs. actual*).

**Refresh.** `scripts\refresh-prices.ps1` refreshes all rows with `source: list` and syncs Azure;
`-Register` adds a weekly Windows scheduled task (Monday 06:00, needs `az login`). Rows with `source: manual`
(negotiated prices; editing a price in the UI sets this) are never overwritten. For an always-on setup run the same
script from an Azure Automation runbook or a pipeline with a managed identity.

**Estimate vs. actual.** The UI compares the estimate for **all teams** with Cost Management for the same **UTC days**
and resource group, per model and token type. Billed spend without gateway metrics has several possible causes - calls
that bypass the gateway, missing telemetry, meter names the mapping doesn't recognise, day boundaries or price
differences - so treat it as a signal to investigate, not proof.

Assumption: cached and cache-write tokens are part of the prompt tokens (OpenAI usage semantics).
The upcoming AI Gateway tier is announced with built-in estimated cost per app, project and model; re-evaluate this page then.

## Recommended model for a shared platform
1. Tag and project-per-team from day one. 2. All traffic through the gateway with per-team keys. 3. Quota per team = hard limit; budget alert = early warning. 4. Monthly FinOps review.
