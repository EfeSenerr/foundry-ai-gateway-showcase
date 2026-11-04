# Budgets and limits: per team, per person, per agent

Two gateway APIs, same routing and metrics (shared policy fragments `portops-llm-*`):

| API | Path | Auth | Limit identity |
|---|---|---|---|
| `openai` | `/openai/...` | APIM subscription key per department (product) | team = product |
| `openai-entra` | `/entra/openai/...` | Entra ID token (`api://portops-ai-gateway`) | team = `Team.*` app role, person = `oid`, agent = `azp` |

"Budget group" = **Entra security group** `sg-portops-team-ops|it|cs` (+ `sg-portops-power-users`), assigned to the app roles `Team.Ops|IT|CS` and `Tier.Power` of the app registration `portops-ai-gateway`. Membership of the group decides which team and tier a person gets in the token.

## Need -> Portal -> Pro-code

| Need | Portal (exact clicks) | Pro-code (file / policy) |
|---|---|---|
| **Team limit** (TPM + monthly tokens) | Azure portal > API Management > *APIs* > `openai-entra` > *All operations* > *Inbound processing* `</>` > edit the `llm-token-limit counter-key="team-..."` lines. Monthly amount: see "Change a quota". | `infra/policies/entra-api-policy.xml` (`llm-token-limit`, key `team-<team>`); key-based API: product policy in `infra/modules/apim.bicep` (`tpm`, `quota` per department). |
| **Person limit** (per user, monthly + TPM) | Same policy editor: `llm-token-limit` with `counter-key="user-<oid>"`. Amount: named value `quota-user-default`. | `entra-api-policy.xml`, block "User limit". |
| **Power tier** (higher limits for some people) | Entra ID > Groups > `sg-portops-power-users` > Members > Add members. Quota = named value `quota-user-power`. | `entra-api-policy.xml` (`Tier.Power` role -> `quota-user-power`, TPM 20000); group/role created in `scripts/setup-entra.ps1`. |
| **Change a quota** | API Management > *Named values* > `quota-team-ops` / `quota-team-it` / `quota-team-cs` / `quota-user-default` / `quota-user-power` > edit *Value* > Save (effective after a short cache delay, ~30 s). No XML edit. | `infra/modules/apim.bicep` (named values, initial values) or `az rest --method patch` on `.../namedValues/<name>`. |
| **Add a person to a budget group** | Entra ID > Groups > `sg-portops-team-<x>` > Members > Add members. The person gets the role in the next token (sign in again). | `scripts/setup-entra.ps1` (groups + app-role assignments); Graph `POST /groups/{id}/members/$ref`. |
| **Team kill switch** | API Management > *Named values* > `team-<x>-enabled` > Value `false` > Save. Blocks key-based AND Entra calls of that team (403). Set `true` to re-enable. | Fragment `infra/policies/fragments/team-enabled.xml`; flip via `az rest` patch (used by `demo/run_all_smoke.py`). |
| **Per-agent / per-person reporting** | Log Analytics > Logs > query `AppMetrics` (see below). | `llm-emit-token-metric` dimensions in `infra/policies/fragments/llm-inbound.xml`. |

Multi-role caveat: if a person is in several team groups, the first `Team.*` role in the token wins. Keep one team group per person.

## 429 vs 403

| Status | Meaning | Client reaction |
|---|---|---|
| **429** | Rate (tokens per minute) exceeded. Transient; `retry-after` header says when. | Back off and retry. |
| **403** `Token quota is exceeded` | Monthly token quota (team or person) used up. Not transient; message contains the time until reset. | Do not retry; ask for more quota. |
| **403** `TeamBudgetExhausted` | Kill switch `team-<x>-enabled=false` (manual or from a budget alert). | Escalate to the team owner / FinOps. |
| **401** | Missing/invalid Entra token or no `Team.*` role. | Sign in / join a budget group. |

Response headers (Entra API): `x-portops-team`, `x-portops-user`, `x-portops-team-remaining-quota`, `x-portops-user-remaining-tokens`, `x-portops-user-remaining-quota`; also present on 403/429.

## From a euro budget to a token quota

Azure Cost Management budgets only alert (with 8-24 h delay); the **gateway quota is the real hard stop**. Translate once and put the result in a named value:

`tokens = budget / blended price per 1M tokens x 1,000,000`

List prices (USD per 1M tokens, input/output, Data Zone, from `infra/pricing/model_prices.json`; verify before quoting):

| Model | Input / Output | Blended (3:1 input:output) | 10 USD buys approx. |
|---|---|---|---|
| gpt-4.1-mini | 0.44 / 1.76 | 0.77 | 13 M tokens |
| gpt-4.1 | 2.20 / 8.80 | 3.85 | 2.6 M tokens |

Example: a person budget of 10 USD/month on gpt-4.1-mini -> `quota-user-default` = 13000000. Mixed models: use the more expensive model for a safe (conservative) quota.

## Per-agent / per-person reporting

Token metrics carry the dimensions `Team`, `User` (UPN), `ClientApp` (`azp`, the calling app/agent registration), `Model`:

```kusto
AppMetrics
| where TimeGenerated > ago(1d) and Name has "token"
| summarize tokens = sum(Sum) by Team = tostring(Properties.Team), User = tostring(Properties.User), ClientApp = tostring(Properties.ClientApp)
| order by tokens desc
```

Give every agent its own app registration to see it as its own `ClientApp`. Key-based calls keep the old dimensions (`Department`, subscription).

## Try it

```powershell
cd demo
python entra_limits_demo.py --user me --burst 5 --show-token-claims   # your az login (demo tenant admin = ops + power)
python entra_limits_demo.py --user it                                   # synthetic test user (see note)
```

Test users (`PORTOPS_TESTUSER_*` in `.env`) are synthetic users of the demo tenant. The script uses ROPC with the public client `portops-demo-client` and falls back to **device code** automatically when ROPC is blocked (MFA / conditional access, error AADSTS50079). In tenants that enforce MFA the synthetic users must register MFA once, otherwise demo with `--user me` and switch teams by changing your group membership (then sign in again for a fresh token).


## € budget → hard stop

Azure budgets only **alert** (cost data lags 8-24 h), so the **gateway quota stays the primary hard stop** (token limits, 429/403 above). The € hard stop adds a cost-driven kill switch on top:

```
Cost Management budget ─┐
                        ├─> action group ag-portops-budget-enforce ─> Logic App la-portops-budget-enforcer
Scheduled query alert  ─┘   (+ email to owner)                         │ managed identity (API Management Service Contributor)
                                                                       v
                                  APIM named value team-<team>-enabled = false ─> policy fragment portops-team-enabled ─> 403 TeamBudgetExhausted
```

The 403 applies to both the key-based `/openai` API and the Entra `/entra/openai` API. `la-portops-budget-reset` (monthly, 1st 00:05 UTC) sets all `team-*-enabled` back to `true`; it also has an on-demand HTTP trigger `Manual_reenable` (`{"team":"cs"|"all"}`).

| Trigger | Resource | Latency | Notes |
|---|---|---|---|
| Near-real-time (demo-able) | scheduled query alerts `alert-portops-spend-{ops,it,cs}` | evaluated every 5 min, plus 5-15 min ingestion/alert delay | KQL over `AppMetrics` (input, cached, cache-write and output tokens) x the shared price table `infra/pricing/model_prices.json` (see [cost-management.md](cost-management.md)); unknown prices count at the highest known price. Estimated spend since midnight UTC per team; thresholds (USD/day) are Bicep param `teamDailyThresholdsUsd` (ops 5, it 1, cs 2). Stateless: while above the threshold it re-fires every 5 min, so a manual re-enable is undone until midnight UTC (or raise the threshold). |
| Real € budget | `budget-portops-<suffix>` (RG, 100/month) and `budget-portops-<suffix>-{ops,it,cs}` | 8-24 h cost-data lag | Per-team budgets filter on the resource tag `project` (values ops/it/cs) and notify the enforce action group at 100% actual. **Caveat:** Azure accepts the tag filter, but Foundry model spend is booked on the single Foundry account, so it is not attributed to a team `project` tag today; the per-team budgets therefore only fire for tagged resources. Per-team spend in € comes from the token-metric alert above (or FinOps allocation in the customer subscription). The RG-level budget notifies the original `ag-portops-<suffix>` (email); add `ag-portops-budget-enforce` as a contact group there only if you want a whole-RG stop (the enforcer needs a team in the payload, otherwise it answers 400). |
| Manual / demo | HTTP POST to the Logic App | seconds | Body `{"team":"cs","action":"disable","reason":"..."}`, a common alert schema payload or a budget payload. The enforcer only disables: `action:"enable"` returns 400 (re-enable via `la-portops-budget-reset`). |

Team resolution in the Logic App: manual `team`, else common-alert-schema `customProperties.team`, else alert rule name or budget name ending in `-ops`/`-it`/`-cs`. The enforcer trigger always disables.

### Try it

```powershell
cd demo
python budget_demo.py --mode status                    # team-*-enabled + estimated USD today per team
python budget_demo.py --team cs --mode simulate        # alert payload -> Logic App -> named value false -> 403 -> reset -> 200
python budget_demo.py --team cs --mode simulate --no-reset
python budget_demo.py --team cs --mode reset        # re-enable via la-portops-budget-reset
```

The callback URL (contains a SAS signature) is read at runtime with `listCallbackUrl`; it is never stored in files (enforcer and reset each have their own URL).

### Best-practice review

Compared with the Azure-Samples/AI-Gateway FinOps framework lab.

| Topic | Status |
|---|---|
| Alert -> action group -> Logic App -> block the team; Logic App identity with least-privilege role | Matches the lab |
| Logic App diagnostics (WorkflowRuntime + AllMetrics) to Log Analytics | Fix 1: added for both Logic Apps |
| Kill switch scope | Deviation: APIM named value instead of suspending the APIM subscription, so it also covers the Entra API |
| Evaluation window | Deviation: daily window (since midnight UTC) |
| Usage source | Deviation: `AppMetrics` (token metrics) instead of `ApiManagementGatewayLlmLog` |
| Prices | One price table (`infra/pricing/model_prices.json`) for UI, workbook and alerts, refreshed weekly from the Retail Prices API and pushed into the deployed alerts; the lab keeps prices in a Log Analytics table fed via a DCR, which avoids rewriting queries |
| Enforcer trigger can only disable (`enable` -> 400); re-enable only via `la-portops-budget-reset` (monthly + `Manual_reenable` `{"team":"cs"\|"all"}`) | Fix 2 |
| Alert noise: `muteActionsDuration: PT1H` on the scheduled query rules (actions/emails at most hourly per team) | Fix 3 |

### Portal tour

1. **Logic App run history:** Resource group > `la-portops-budget-enforcer` > *Overview* > *Runs history* (open a run to see each action: Resolve team, Set APIM named value...). *Logic app designer* shows the flow and is editable.
2. **Alert rule:** Monitor > Alerts > Alert rules > `alert-portops-spend-cs` (query, threshold, action group); fired alerts under Monitor > Alerts.
3. **Budget:** Cost Management > Budgets > `budget-portops-<suffix>` (and the `-ops/-it/-cs` budgets with the `project` tag filter).
4. **Effect:** API Management > Named values > `team-cs-enabled` (`false` = blocked); the next gateway call returns 403 `TeamBudgetExhausted` after a few seconds (named-value cache).
5. **Action group:** Monitor > Alerts > Action groups > `ag-portops-budget-enforce` (Logic App receiver + email).
