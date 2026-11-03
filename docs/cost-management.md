# Cost management - three levels

Facts from MS Learn "Plan and manage costs" (checked 2026-10-21); prices below are illustrative, verify.

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

Illustrative price effect (list price per 1M tokens, input/output, verify): gpt-4.1-mini 0.40 / 1.60 vs gpt-4.1 2.00 / 8.00 - routing simple questions to the small model is ~5x cheaper.

## Level 3 - Operations
- **Cost exports** to storage (daily) -> Power BI / FinOps hub for chargeback.
- **KQL** in `observability/queries.kql`: `tokens_per_dept`, `cost_per_dept` (tokens x illustrative price), `throttled_429`.
- **Workbook** (optional) over the same queries: tokens, estimated cost, status codes per department.
- Reconcile estimated (tokens x price) with invoiced (Cost analysis) monthly.

## Recommended model for a shared platform
1. Tag and project-per-team from day one. 2. All traffic through the gateway with per-team keys. 3. Quota per team = hard limit; budget alert = early warning. 4. Monthly FinOps review.
