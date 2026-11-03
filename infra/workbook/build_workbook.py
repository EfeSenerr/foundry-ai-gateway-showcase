"""Generates portops-ai-gateway.workbook.json (the Azure Workbook serializedData).

  python infra/workbook/build_workbook.py            # writes the JSON next to this file
  python infra/workbook/build_workbook.py --validate # also runs every KQL against the workspace in .env

The JSON is the deliverable (loaded by workbook.bicep); this script only keeps the KQL readable.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = "__WORKSPACE_ID__"  # replaced by workbook.bicep with the workspace resource id
# replaced by workbook.bicep at deploy time
SUB = "__SUBSCRIPTION_ID__"
RG = "__RESOURCE_GROUP__"
SFX = "__SUFFIX__"

PRICES = 'let prices = datatable(Model:string, PriceIn:double, PriceOut:double)\n  ["gpt-4.1-mini", 0.40, 1.60, "gpt-4.1", 2.00, 8.00];\n'
MODEL_NORM = ('| extend Model = iff(ModelRaw startswith "gpt-4.1-mini", "gpt-4.1-mini", '
              'iff(ModelRaw startswith "gpt-4.1", "gpt-4.1", ModelRaw))\n')
METRIC_BASE = ('AppMetrics\n| extend Department = tostring(Properties["Department"]), '
               'ModelRaw = tostring(Properties["Model"])\n| where Department in ({Department})\n')
GW = 'ApiManagementGatewayLogs\n| where ProductId in ({Department})\n'
COST = (PRICES + METRIC_BASE + '| where Name in ("Prompt Tokens", "Completion Tokens")\n' + MODEL_NORM +
        '| summarize Prompt = sumif(Sum, Name == "Prompt Tokens"), Completion = sumif(Sum, Name == "Completion Tokens") by Department, Model\n'
        '| join kind=leftouter prices on Model\n'
        '| extend EstCostUSD = round(Prompt / 1e6 * PriceIn + Completion / 1e6 * PriceOut, 4)\n')

items: list[dict] = []
queries: list[tuple[str, str]] = []
_n = 0


def md(text: str, name: str | None = None, cond: dict | None = None):
    global _n
    _n += 1
    it = {"type": 1, "content": {"json": text}, "name": name or f"text-{_n}"}
    if cond:
        it["conditionalVisibility"] = cond
    items.append(it)


def q(title: str, kql: str, viz: str, size: int = 1, extra: dict | None = None, cond: dict | None = None,
      name: str | None = None):
    global _n
    _n += 1
    name = name or f"q-{_n}"
    queries.append((name, kql))
    content = {
        "version": "KqlItem/1.0", "query": kql, "size": size, "title": title,
        "timeContextFromParameter": "TimeRange", "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces", "crossComponentResources": [WS],
        "visualization": viz,
    }
    content.update(extra or {})
    it = {"type": 3, "content": content, "name": name}
    if cond:
        it["conditionalVisibility"] = cond
    if size in (1, 4) and viz in ("tiles", "barchart", "timechart", "table"):
        it["customWidth"] = "50"
    items.append(it)


def wide(it_title_kw=None):
    items[-1].pop("customWidth", None)


def heading(t: str):
    md(f"## {t}")


# ---- parameters -------------------------------------------------------------------------
items.append({"type": 9, "name": "parameters", "content": {
    "version": "KqlParameterItem/1.0",
    "parameters": [
        {"id": "p-time", "version": "KqlParameterItem/1.0", "name": "TimeRange", "label": "Time range", "type": 4,
         "isRequired": True, "value": {"durationMs": 14400000},
         "typeSettings": {"selectableValues": [{"durationMs": d} for d in
                                               (900000, 3600000, 14400000, 43200000, 86400000, 259200000, 604800000)],
                          "allowCustom": True}},
        {"id": "p-dept", "version": "KqlParameterItem/1.0", "name": "Department", "label": "Department", "type": 2,
         "isRequired": True, "multiSelect": True, "quote": "'", "delimiter": ",",
         "query": "ApiManagementGatewayLogs\n| where isnotempty(ProductId)\n| distinct ProductId\n"
                  "| union (datatable(ProductId:string)['dept-ops','dept-it','dept-cs'])\n"
                  "| distinct ProductId\n| order by ProductId asc",
         "typeSettings": {"additionalResourceOptions": ["value::all"], "showDefault": False},
         "timeContext": {"durationMs": 604800000}, "defaultValue": "value::all",
         "queryType": 0, "resourceType": "microsoft.operationalinsights/workspaces",
         "crossComponentResources": [WS]},
        {"id": "p-rid", "version": "KqlParameterItem/1.0", "name": "RequestId", "label": "apim-request-id",
         "type": 1, "isRequired": False, "value": "",
         "description": "Paste the apim-request-id response header (section: Request lookup)"},
    ],
    "style": "pills", "queryType": 0, "resourceType": "microsoft.operationalinsights/workspaces"}})

md("# PortOps AI Gateway – Cost & Operations\n"
   "Live view of the Azure API Management **AI Gateway** in front of Microsoft Foundry. Gateway logs and "
   "token metrics arrive in Log Analytics ~2–5 min after a call. Departments = APIM products "
   "(`dept-ops`, `dept-it`, `dept-cs`). **Costs below are estimates from an illustrative list price** "
   "(gpt-4.1-mini $0.40 in / $1.60 out, gpt-4.1 $2.00 / $8.00 per 1M tokens); the actual bill is in "
   "Cost Management.")

# ---- a. KPIs ----------------------------------------------------------------------------
heading("1. Key numbers")
kpi = (PRICES +
       "let tok = " + METRIC_BASE +
       '| where Name == "Total Tokens" | summarize v = sum(Sum);\n'
       "let cost = " + METRIC_BASE + '| where Name in ("Prompt Tokens", "Completion Tokens")\n' + MODEL_NORM +
       '| summarize Prompt = sumif(Sum, Name == "Prompt Tokens"), Completion = sumif(Sum, Name == "Completion Tokens") by Model\n'
       '| join kind=leftouter prices on Model\n'
       '| summarize v = sum(Prompt / 1e6 * PriceIn + Completion / 1e6 * PriceOut);\n'
       "let gw = " + GW + "| summarize req = count(), r429 = countif(ResponseCode == 429), r5xx = countif(ResponseCode >= 500);\n"
       "union (gw | project Metric = \"1 Requests\", Value = todouble(req)),\n"
       "      (gw | project Metric = \"4 HTTP 429 (throttled)\", Value = todouble(r429)),\n"
       "      (gw | project Metric = \"5 HTTP 5xx\", Value = todouble(r5xx)),\n"
       "      (tok | project Metric = \"2 Total tokens\", Value = todouble(v)),\n"
       "      (cost | project Metric = \"3 Est. cost USD (illustrative)\", Value = todouble(round(v, 4)))\n"
       "| extend Metric = substring(Metric, 2)\n| order by Metric asc")
kpi = kpi.replace("| extend Metric = substring(Metric, 2)\n| order by Metric asc",
                  "| order by Metric asc\n| extend Metric = substring(Metric, 2)")
q("Requests · tokens · est. cost · 429 · 5xx", kpi, "tiles", 4, {
    "tileSettings": {"titleContent": {"columnMatch": "Metric", "formatter": 1},
                     "leftContent": {"columnMatch": "Value", "formatter": 12,
                                     "formatOptions": {"palette": "auto"},
                                     "numberFormat": {"unit": 17, "options": {"style": "decimal", "maximumFractionDigits": 2}}},
                     "showBorder": True, "size": "auto"}}, name="kpi")
wide()

# ---- b. tokens ---------------------------------------------------------------------------
heading("2. Tokens per department and model")
q("Total tokens over time per department",
  METRIC_BASE + '| where Name == "Total Tokens"\n| summarize Tokens = sum(Sum) by bin(TimeGenerated, 5m), Department\n| order by TimeGenerated asc',
  "timechart", 0, {"chartSettings": {"xAxis": "TimeGenerated", "yAxis": ["Tokens"], "group": "Department", "createOtherGroup": 0}})
wide()
q("Tokens per department (prompt vs completion)",
  METRIC_BASE + '| where Name in ("Prompt Tokens", "Completion Tokens")\n| summarize Tokens = sum(Sum) by Department, Name\n| order by Department asc',
  "barchart", 1, {"chartSettings": {"xAxis": "Department", "yAxis": ["Tokens"], "group": "Name"}})
q("Tokens per model",
  METRIC_BASE + '| where Name == "Total Tokens"\n' + MODEL_NORM + '| summarize Tokens = sum(Sum) by Model\n| order by Tokens desc',
  "barchart", 1, {"chartSettings": {"xAxis": "Model", "yAxis": ["Tokens"]}})

# ---- c. cost -----------------------------------------------------------------------------
heading("3. Estimated cost per department (chargeback)")
md("_Estimate = tokens × illustrative list price. The **actual** bill: Cost Management → Cost analysis → "
   "group by tag `project` (and by resource / meter). Tokens are the per-department driver; the `project` tag "
   "is the per-Foundry-project chargeback key._")
q("Estimated cost per department and model (USD, illustrative)",
  COST + "| project Department, Model, Prompt, Completion, EstCostUSD\n| order by EstCostUSD desc", "table", 1,
  {"gridSettings": {"formatters": [{"columnMatch": "EstCostUSD", "formatter": 8, "formatOptions": {"palette": "greenRed"}}]}})
q("Estimated cost per department (USD)",
  COST + "| summarize EstCostUSD = sum(EstCostUSD) by Department\n| order by EstCostUSD desc", "barchart", 1,
  {"chartSettings": {"xAxis": "Department", "yAxis": ["EstCostUSD"]}})

# ---- d. status ---------------------------------------------------------------------------
heading("4. Status codes, throttling and errors")
q("Requests by status code over time per department",
  GW + "| summarize Requests = count() by bin(TimeGenerated, 5m), Series = strcat(ProductId, \" \", tostring(ResponseCode))\n| order by TimeGenerated asc",
  "timechart", 0, {"chartSettings": {"xAxis": "TimeGenerated", "yAxis": ["Requests"], "group": "Series", "createOtherGroup": 0}})
wide()
q("429 throttling per department (llm-token-limit)",
  GW + "| summarize Total = count(), Throttled = countif(ResponseCode == 429) by ProductId\n"
       "| extend ThrottledPct = round(100.0 * Throttled / Total, 1)\n| order by Throttled desc", "table", 1,
  {"gridSettings": {"formatters": [{"columnMatch": "ThrottledPct", "formatter": 8, "formatOptions": {"palette": "orange"}}]}})
q("5xx origin: gateway vs backend",
  GW + "| where ResponseCode >= 500\n"
       "| extend Chaos = ResponseHeaders contains \"x-portops-chaos\"\n"
       "| extend Origin = case(Chaos, \"gateway (chaos switch)\", isempty(BackendResponseCode) or BackendResponseCode == 0, \"gateway (policy/timeout)\", BackendResponseCode >= 500, \"backend (Foundry)\", \"gateway-transformed\")\n"
       "| summarize Errors = count() by Origin, ProductId\n| order by Errors desc", "barchart", 1,
  {"chartSettings": {"xAxis": "Origin", "yAxis": ["Errors"], "group": "ProductId"}})
q("5xx detail (ResponseCode = what the client saw, BackendResponseCode = what Foundry returned)",
  GW + "| where ResponseCode >= 500\n"
       "| extend Origin = case(ResponseHeaders contains \"x-portops-chaos\", \"gateway (chaos switch)\", isempty(BackendResponseCode) or BackendResponseCode == 0, \"gateway (policy/timeout)\", BackendResponseCode >= 500, \"backend (Foundry)\", \"gateway-transformed\")\n"
       "| project TimeGenerated, CorrelationId, ProductId, ResponseCode, BackendResponseCode, Origin, TotalTime, BackendTime, LastErrorReason, LastErrorMessage\n"
       "| order by TimeGenerated desc\n| take 50", "table", 1)
wide()

# ---- e. latency --------------------------------------------------------------------------
heading("5. Latency (successful calls, ms)")
LAT = ("ApiManagementGatewayLlmLog\n| where isnotempty(ModelName)\n| distinct CorrelationId, ModelName\n"
       "| join kind=inner (ApiManagementGatewayLogs | where ResponseCode == 200 | where ProductId in ({Department})\n"
       "                   | project TimeGenerated, CorrelationId, ProductId, TotalTime, BackendTime) on CorrelationId\n")
q("p50 / p95 per model",
  LAT + "| summarize Requests = count(), p50_total = percentile(TotalTime, 50), p95_total = percentile(TotalTime, 95), "
        "p50_backend = percentile(BackendTime, 50), p95_backend = percentile(BackendTime, 95) by ModelName\n| order by Requests desc",
  "table", 1)
q("p50 / p95 per department",
  LAT + "| summarize Requests = count(), p50_total = percentile(TotalTime, 50), p95_total = percentile(TotalTime, 95), "
        "p50_backend = percentile(BackendTime, 50), p95_backend = percentile(BackendTime, 95) by ProductId\n| order by Requests desc",
  "table", 1)
q("p95 total latency over time per department",
  LAT + "| summarize p95 = percentile(TotalTime, 95) by bin(TimeGenerated, 5m), ProductId\n| order by TimeGenerated asc",
  "timechart", 0, {"chartSettings": {"xAxis": "TimeGenerated", "yAxis": ["p95"], "group": "ProductId", "createOtherGroup": 0}})
wide()

# ---- f. lookup ---------------------------------------------------------------------------
heading("6. Request lookup (paste an apim-request-id)")
md("Every response carries an `apim-request-id` header. Paste it into the **apim-request-id** parameter above.")
cond = {"parameterName": "RequestId", "comparison": "isNotEqualTo", "value": ""}
q("Gateway log row",
  'let rid = "{RequestId}";\nApiManagementGatewayLogs\n| where CorrelationId == rid\n'
  "| project TimeGenerated, CorrelationId, ProductId, ApimSubscriptionId, ApiId, Method, Url, ResponseCode, BackendResponseCode, TotalTime, BackendTime, BackendId, LastErrorReason, LastErrorMessage, ResponseHeaders",
  "table", 1, cond=cond, name="lookup-gw")
wide()
q("Matching LLM log (tokens, model, deployment)",
  'let rid = "{RequestId}";\nApiManagementGatewayLlmLog\n| where CorrelationId == rid\n'
  "| project TimeGenerated, CorrelationId, ModelName, DeploymentName, PromptTokens, CompletionTokens, TotalTokens, IsStreamCompletion, RequestMessages, ResponseMessages",
  "table", 1, cond=cond, name="lookup-llm")
wide()
q("Foundry diagnostic rows (same CorrelationId, or the same call by time/deployment)",
  'let rid = "{RequestId}";\nlet gw = ApiManagementGatewayLogs | where CorrelationId == rid | project gwTime = TimeGenerated, gwUrl = Url;\n'
  "AzureDiagnostics\n| where ResourceProvider == \"MICROSOFT.COGNITIVESERVICES\" and Category == \"RequestResponse\"\n"
  "| extend p = parse_json(properties_s)\n"
  "| where CorrelationId == rid or TimeGenerated between ((toscalar(gw | project gwTime) - 10s) .. (toscalar(gw | project gwTime) + 30s))\n"
  "| project TimeGenerated, CorrelationId, OperationName, ResultSignature, DurationMs, Model = tostring(p.modelName), PromptTokens = toint(p.promptTokens), CompletionTokens = toint(p.completionTokens)\n"
  "| order by TimeGenerated asc",
  "table", 1, cond=cond, name="lookup-foundry")
wide()

# ---- g. foundry --------------------------------------------------------------------------
heading(f"7. Foundry account (aif-portops-{SFX}) diagnostics")
FD = ('AzureDiagnostics\n| where ResourceProvider == "MICROSOFT.COGNITIVESERVICES" and Category == "RequestResponse"\n'
      "| extend p = parse_json(properties_s)\n| where tostring(p.apiName) != \"Azure AI Projects API\"\n")
q("Foundry requests by status code",
  FD + "| summarize Requests = count() by bin(TimeGenerated, 5m), Status = tostring(ResultSignature)\n| order by TimeGenerated asc",
  "timechart", 0, {"chartSettings": {"xAxis": "TimeGenerated", "yAxis": ["Requests"], "group": "Status", "createOtherGroup": 0}})
q("Foundry latency per operation (ms)",
  FD + "| summarize Requests = count(), p50_ms = percentile(DurationMs, 50), p95_ms = percentile(DurationMs, 95) by OperationName, Model = tostring(p.modelName)\n| order by Requests desc",
  "table", 1)
q("Foundry-side token usage per model (what the backend actually served)",
  FD + "| summarize Requests = count(), PromptTokens = sum(toint(p.promptTokens)), CompletionTokens = sum(toint(p.completionTokens)) by Model = tostring(p.modelName)\n| where isnotempty(Model)",
  "table", 1)

# ---- i. per user / per app / MCP / blocked / budget (phase 2) ----------------------------
# Team = Team dimension (Entra API, MCP) or the department of key-based calls ("dept-cs" -> "cs").
TEAM = ('| extend Team = iff(isnotempty(tostring(Properties["Team"])), tostring(Properties["Team"]), '
        'replace_string(tostring(Properties["Department"]), "dept-", ""))\n')
USERX = ('| extend User = iff(isnotempty(tostring(Properties["User"])), tostring(Properties["User"]), "(department key - no user)")\n')
APPX = ('| extend ClientApp = case(isnotempty(tostring(Properties["ClientApp"])), tostring(Properties["ClientApp"]), '
        'isnotempty(tostring(Properties["Subscription ID"])), tostring(Properties["Subscription ID"]), "(unknown)")\n')
TOK_BASE = ('AppMetrics\n| where Name in ("Prompt Tokens", "Completion Tokens", "Total Tokens")\n'
            '| extend ModelRaw = tostring(Properties["Model"])\n' + MODEL_NORM + TEAM + USERX + APPX)


def cost_by(keys: str, extra_where: str = "") -> str:
    return (PRICES + TOK_BASE + extra_where +
            f'| summarize Prompt = sumif(Sum, Name == "Prompt Tokens"), Completion = sumif(Sum, Name == "Completion Tokens"), '
            f'Total = sumif(Sum, Name == "Total Tokens") by {keys}, Model\n'
            '| join kind=leftouter prices on Model\n'
            '| extend EstCostUSD = round(Prompt / 1e6 * coalesce(PriceIn, 0.40) + Completion / 1e6 * coalesce(PriceOut, 1.60), 4)\n'
            f'| summarize Prompt = sum(Prompt), Completion = sum(Completion), Total = sum(Total), EstCostUSD = round(sum(EstCostUSD), 4) by {keys}\n')


heading("8. Per user and team (Entra API)")
md("Calls through `/entra/openai` carry the signed-in **User** and **Team** (Entra group) as metric dimensions. "
   "Key-based department calls have no user and show up as `(department key - no user)`. Costs are illustrative estimates.")
q("Tokens and est. cost per team",
  cost_by("Team") + "| order by EstCostUSD desc", "table", 1,
  {"gridSettings": {"formatters": [{"columnMatch": "EstCostUSD", "formatter": 8, "formatOptions": {"palette": "greenRed"}}]}},
  name="user-team")
q("Top users by tokens (est. cost, USD)",
  cost_by("User, Team", '| where User != "(department key - no user)"\n') + "| order by Total desc\n| take 20", "table", 1,
  {"gridSettings": {"formatters": [{"columnMatch": "EstCostUSD", "formatter": 8, "formatOptions": {"palette": "greenRed"}}]}},
  name="user-top")
q("Tokens over time per user (Entra API)",
  TOK_BASE + '| where Name == "Total Tokens" and User != "(department key - no user)"\n'
  "| summarize Tokens = sum(Sum) by bin(TimeGenerated, 5m), User\n| order by TimeGenerated asc",
  "timechart", 0, {"chartSettings": {"xAxis": "TimeGenerated", "yAxis": ["Tokens"], "group": "User", "createOtherGroup": 0}},
  name="user-time")
wide()

heading("9. Per calling app / agent")
md("`ClientApp` = the Entra application id (`azp`) of the caller on the Entra API; for key-based calls it falls back "
   "to the APIM subscription id (one per department/team).")
q("Tokens and est. cost per calling app",
  cost_by("ClientApp, Team") + "| order by Total desc", "table", 1,
  {"gridSettings": {"formatters": [{"columnMatch": "EstCostUSD", "formatter": 8, "formatOptions": {"palette": "greenRed"}}]}},
  name="app-table")
q("Tokens per calling app",
  TOK_BASE + '| where Name == "Total Tokens"\n| summarize Tokens = sum(Sum) by ClientApp\n| order by Tokens desc',
  "barchart", 1, {"chartSettings": {"xAxis": "ClientApp", "yAxis": ["Tokens"]}}, name="app-bar")

MCP = ('AppMetrics\n| where Name == "McpToolCalls"\n| extend Team = tostring(Properties["Team"]), '
       'Tool = tostring(Properties["Tool"]), Result = tostring(Properties["Result"]), '
       'ClientApp = tostring(Properties["ClientApp"])\n')
heading("10. MCP tool calls (through the gateway)")
q("Tool calls per team and tool (allowed vs blocked)",
  MCP + "| summarize Calls = sum(Sum) by Team, Tool, Result\n| order by Team asc, Calls desc", "table", 1,
  {"gridSettings": {"formatters": [{"columnMatch": "Result", "formatter": 18, "formatOptions": {
      "thresholdsOptions": "colors", "thresholdsGrid": [
          {"operator": "==", "thresholdValue": "blocked", "representation": "redBright", "text": "{0}"},
          {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}"}]}}]}},
  name="mcp-table")
q("Allowed vs blocked calls per team",
  MCP + "| summarize Calls = sum(Sum) by Team, Result\n| order by Team asc", "barchart", 1,
  {"chartSettings": {"xAxis": "Team", "yAxis": ["Calls"], "group": "Result"}}, name="mcp-bar")
q("Tool calls over time (allowed vs blocked)",
  MCP + "| summarize Calls = sum(Sum) by bin(TimeGenerated, 5m), Result\n| order by TimeGenerated asc",
  "timechart", 0, {"chartSettings": {"xAxis": "TimeGenerated", "yAxis": ["Calls"], "group": "Result", "createOtherGroup": 0}},
  name="mcp-time")
wide()
q("Blocked tool calls (team, tool, count, last seen)",
  MCP + '| where Result == "blocked"\n| summarize Calls = sum(Sum), LastSeen = max(TimeGenerated) by Team, Tool\n| order by LastSeen desc',
  "table", 1, name="mcp-blocked")
wide()

# Heuristics: reason = ApiId + ResponseCode + LastErrorReason/Source (policy errors) + x-portops-mcp-result header.
BLOCK = ('ApiManagementGatewayLogs\n| where ResponseCode in (401, 403, 429)\n'
         '| extend McpResult = tostring(parse_json(ResponseHeaders)["x-portops-mcp-result"])\n'
         '| extend Reason = case(\n'
         '    ApiId startswith "mcp-" and ResponseCode == 401, "401 MCP: missing/invalid team key",\n'
         '    ApiId startswith "mcp-" and ResponseCode == 403 and McpResult == "wrong-team", "403 MCP: wrong team for this MCP path",\n'
         '    ApiId startswith "mcp-" and ResponseCode == 403, "403 MCP: forbidden (other)",\n'
         '    ResponseCode == 401, "401 Entra: no/invalid token",\n'
         '    ResponseCode == 403 and LastErrorReason == "OpenAITokenQuotaExceeded", "403 quota exhausted (team/user)",\n'
         '    ResponseCode == 403 and ApiId == "openai" and isempty(LastErrorReason), "403 TeamBudgetExhausted (kill switch)",\n'
         '    ResponseCode == 429, "429 TPM exceeded (rate limit)",\n'
         '    strcat(tostring(ResponseCode), " other"))\n')
heading("11. Blocked requests by reason")
md("Derived by heuristics from the gateway log: **401** on `openai-entra` = no/invalid Entra token (`validate-azure-ad-token`); "
   "**403 + LastErrorReason `OpenAITokenQuotaExceeded`** = monthly team/user quota exhausted; **403 on `openai` with no policy error** "
   "= team kill switch (`TeamBudgetExhausted`, set by the budget enforcer); **403 on `mcp-*` + header `x-portops-mcp-result=wrong-team`** = "
   "team key used on another team's MCP path; **401 on `mcp-*`** = missing/invalid MCP key; **429** = tokens-per-minute limit "
   "(`OpenAITokenLimitExceeded`). Tool-level blocks are JSON-RPC errors (HTTP 200) - see section 10.")
q("Blocked requests by reason",
  BLOCK + "| summarize Requests = count() by Reason\n| order by Requests desc", "barchart", 1,
  {"chartSettings": {"xAxis": "Reason", "yAxis": ["Requests"]}}, name="blk-reason")
q("Blocked requests over time by reason",
  BLOCK + "| summarize Requests = count() by bin(TimeGenerated, 5m), Reason\n| order by TimeGenerated asc",
  "timechart", 0, {"chartSettings": {"xAxis": "TimeGenerated", "yAxis": ["Requests"], "group": "Reason", "createOtherGroup": 0}},
  name="blk-time")
wide()
q("Latest blocked requests",
  BLOCK + "| extend U = tostring(parse_json(ResponseHeaders)[\"x-portops-user\"])\n"
          "| extend Who = case(isnotempty(U), U, isnotempty(ApimSubscriptionId), ApimSubscriptionId, ProductId)\n"
          "| project TimeGenerated, Reason, ApiId, ResponseCode, Who, LastErrorSource, LastErrorReason, CorrelationId\n"
          "| order by TimeGenerated desc\n| take 50", "table", 1, name="blk-list")
wide()

heading("12. Budget enforcement")
md("The budget enforcer (Logic App `la-portops-budget-enforcer`) flips the APIM named value `team-<team>-enabled` "
   "when a spend alert fires; the gateway then answers **403 TeamBudgetExhausted**. Thresholds below are the "
   "spend-alert limits (ops 5 / it 1 / cs 2 USD per day, illustrative estimate).")
q("Estimated spend today (UTC) per team vs spend-alert threshold",
  PRICES + "let thr = datatable(Team:string, ThresholdUSD:double) [\"ops\", 5.0, \"it\", 1.0, \"cs\", 2.0];\n"
  + TOK_BASE + '| where TimeGenerated >= startofday(now()) and Name in ("Prompt Tokens", "Completion Tokens")\n'
  '| summarize Prompt = sumif(Sum, Name == "Prompt Tokens"), Completion = sumif(Sum, Name == "Completion Tokens") by Team, Model\n'
  '| join kind=leftouter prices on Model\n'
  '| summarize EstUSDToday = round(sum(Prompt / 1e6 * coalesce(PriceIn, 0.40) + Completion / 1e6 * coalesce(PriceOut, 1.60)), 4) by Team\n'
  '| join kind=rightouter thr on Team\n'
  '| project Team = Team1, EstUSDToday = coalesce(EstUSDToday, 0.0), ThresholdUSD\n'
  '| extend PctOfThreshold = round(100.0 * EstUSDToday / ThresholdUSD, 1), '
  'Status = iff(EstUSDToday >= ThresholdUSD, "ALERT (kill switch)", iff(EstUSDToday >= 0.8 * ThresholdUSD, "warning", "ok"))\n'
  '| order by Team asc', "table", 1,
  {"gridSettings": {"formatters": [{"columnMatch": "PctOfThreshold", "formatter": 8, "formatOptions": {"palette": "greenRed"}}]}},
  name="bud-threshold")
q("Requests rejected by the team kill switch (403 TeamBudgetExhausted)",
  "ApiManagementGatewayLogs\n| where ApiId == \"openai\" and ResponseCode == 403 and isempty(LastErrorReason)\n"
  "| summarize Rejected = count(), First = min(TimeGenerated), Last = max(TimeGenerated) by ProductId\n| order by Last desc",
  "table", 1, name="bud-403")
q("Budget enforcer Logic App runs",
  "AzureDiagnostics\n| where ResourceProvider == \"MICROSOFT.LOGIC\" and Category == \"WorkflowRuntime\"\n"
  "| where OperationName == \"Microsoft.Logic/workflows/workflowRunCompleted\"\n"
  "| where Resource =~ \"LA-PORTOPS-BUDGET-ENFORCER\"\n"
  "| project TimeGenerated, Run = column_ifexists(\"resource_runId_s\", \"\"), Status = column_ifexists(\"status_s\", \"\"), "
  "Code = column_ifexists(\"code_s\", \"\"), Start = column_ifexists(\"startTime_t\", datetime(null)), "
  "End = column_ifexists(\"endTime_t\", datetime(null))\n| order by TimeGenerated desc\n| take 30",
  "table", 1, name="bud-runs")
wide()
md("_Logic App runs need the workflow's diagnostic setting (WorkflowRuntime -> Log Analytics). If the table above is "
   "empty, open the Logic App > **Overview > Runs history** (link below)._")

# ---- h. links ----------------------------------------------------------------------------
heading("13. Open in the portal")
scope = f"%2Fsubscriptions%2F{SUB}%2FresourceGroups%2F{RG}"
rid = f"https://portal.azure.com/#@/resource/subscriptions/{SUB}/resourceGroups/{RG}/providers"
md("\n".join([
    f"- [Cost analysis – resource group {RG}](https://portal.azure.com/#view/Microsoft_Azure_CostManagement/Menu/~/costanalysis/scope/{scope}) "
    "(group by tag `project`, then by resource)",
    f"- [Budgets – budget-portops-{SFX}](https://portal.azure.com/#view/Microsoft_Azure_CostManagement/Menu/~/budgets/scope/{scope})",
    f"- [API Management apim-portops-{SFX}]({rid}/Microsoft.ApiManagement/service/apim-portops-{SFX}/overview) → left menu **Monitoring → Analytics**",
    f"- [Application Insights appi-portops-{SFX}]({rid}/microsoft.insights/components/appi-portops-{SFX}/overview) → **Monitoring → Metrics** → namespace `portops`",
    f"- [Logic App la-portops-budget-enforcer – run history]({rid}/Microsoft.Logic/workflows/la-portops-budget-enforcer/logicApp)",
    "- [Foundry portal (ai.azure.com)](https://ai.azure.com) → project → **Operate / Monitor**",
    f"- [Foundry account aif-portops-{SFX}]({rid}/Microsoft.CognitiveServices/accounts/aif-portops-{SFX}/overview)",
]))

workbook = {"version": "Notebook/1.0", "items": items, "isLocked": False,
            "fallbackResourceIds": [WS],
            "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json"}
out = HERE / "portops-ai-gateway.workbook.json"
out.write_text(json.dumps(workbook, indent=2), encoding="utf-8")
print(f"wrote {out} ({len(queries)} queries)")

if "--validate" in sys.argv:
    from datetime import timedelta
    from azure.identity import AzureCliCredential
    from azure.monitor.query import LogsQueryClient, LogsQueryStatus
    from dotenv import dotenv_values
    ws = dotenv_values(HERE.parent.parent / ".env")["LOG_ANALYTICS_WORKSPACE_ID"]
    client = LogsQueryClient(AzureCliCredential())
    bad = 0
    for name, kql in queries:
        k = kql.replace("{Department}", "'dept-ops','dept-it','dept-cs'")
        rid_val = sys.argv[sys.argv.index("--rid") + 1] if "--rid" in sys.argv else "00000000-0000-0000-0000-000000000000"
        k = k.replace("{RequestId}", rid_val)
        try:
            r = client.query_workspace(ws, k, timespan=timedelta(days=7))
            rows = len(r.tables[0].rows) if r.tables else 0
            print(f"OK   {name:16} rows={rows}" + ("" if r.status == LogsQueryStatus.SUCCESS else " PARTIAL"))
        except Exception as e:
            bad += 1
            print(f"FAIL {name:16} {str(e)[:300]}")
    sys.exit(1 if bad else 0)


