"""Token usage and cost per team, person and model - one price source for reporting and enforcement.

infra/pricing/model_prices.json  = the single price table (per model: price per 1M tokens for input, cached input,
                                   cache write and output, plus the pricing variant used to look up list prices).
infra/pricing/price-table.kql    = generated from it (save_prices). Used by the spend alerts that drive the budget
                                   kill switch (budget-enforcer.bicep), the workbook and observability/queries.kql.
                                   sync_azure() pushes a changed table into the deployed alerts and workbook.

Estimated cost = gateway token metrics (Log Analytics `AppMetrics`, from llm-emit-token-metric) x that table.
Actual cost    = Cost Management (real bill per model meter, no per-person split, 8-24 h delay).

Missing prices: reporting shows those tokens as *unpriced* (estimate incomplete); enforcement (spend alert) prices
them at the highest known price of that token type, so an unknown model can never slip under the budget.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date

import requests

import common

PRICING_DIR = common.REPO_ROOT / "infra" / "pricing"
PRICE_FILE = PRICING_DIR / "model_prices.json"
PRICE_KQL = PRICING_DIR / "price-table.kql"
TOKEN_TYPES = ("input", "cached", "cache_write", "output")
TOKEN_LABELS = {"input": "Input", "cached": "Cached input", "cache_write": "Cache write", "output": "Output"}

# Pricing variant -> words used in Azure meter names
DEPLOYMENT_TYPES = {"Data Zone": ("Data Zone", "DZone", "DZ"), "Global": ("glbl", "Gl"), "Regional": ("regnl", "Rg", "Regnl")}
PLANS = {"Standard": "Std", "Flex": "Flex", "Priority": "PP"}
CONTEXTS = {"Short": "ShortCo", "Long": "LongCo"}
DEFAULT_VARIANT = {"deployment_type": "Data Zone", "plan": "Standard", "context": "Short"}
RETAIL_API = "https://prices.azure.com/api/retail/prices"
ALERT_PREFIX = "alert-portops-spend-"
KQL_BEGIN, KQL_END = "// PRICES-BEGIN", "// PRICES-END"

# "gpt 4.1 mini cached Inp DZone Tokens" (per 1K)
_OLD = re.compile(r"^(?P<model>.+?) (?P<cached>cached )?(?P<dir>Inp|Outp) (?P<scope>Data Zone|DZone|glbl|regnl) Tokens$")
# "6-luna ShortCo Cd Inp Std DZ 1M Tokens" (per 1M)
_NEW = re.compile(r"^(?P<model>\S+) (?P<ctx>ShortCo|LongCo) (?P<kind>Cd Inp|Cd Wr|Inp|Opt) "
                  r"(?P<plan>Std|Flex|PP) (?P<scope>DZ|Gl|Rg\w*) 1M Tokens$")


# ------------------------------------------------------------------------------------------- Azure access
def _az(*args: str) -> str:
    az = "az.cmd" if sys.platform == "win32" else "az"
    r = subprocess.run([az, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"az {' '.join(args[:3])} failed: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def _token(resource: str) -> str:
    """Token for the PoC subscription's tenant, even if another subscription is the az default."""
    return _az("account", "get-access-token", "--subscription", common.env("AZURE_SUBSCRIPTION_ID"),
               "--resource", resource, "--query", "accessToken", "-o", "tsv")


def _arm(method: str, path: str, body: dict | None = None, api: str = "2022-06-15") -> dict:
    import time
    sep = "&" if "?" in path else "?"
    headers = {"Authorization": f"Bearer {_token('https://management.azure.com')}"}
    for attempt in range(4):
        r = requests.request(method, f"https://management.azure.com{path}{sep}api-version={api}",
                             headers=headers, json=body, timeout=120)
        if r.status_code != 429 or attempt == 3:
            break
        # Cost Management throttles aggressively; honour its retry hint
        wait = r.headers.get("x-ms-ratelimit-microsoft.costmanagement-entity-retry-after") or r.headers.get("Retry-After") or 10
        time.sleep(min(float(wait), 60))
    r.raise_for_status()
    return r.json() if r.content else {}


def _rg_path() -> str:
    return f"/subscriptions/{common.env('AZURE_SUBSCRIPTION_ID')}/resourceGroups/{common.env('AZURE_RESOURCE_GROUP')}"


# ------------------------------------------------------------------------------------------- usage
USAGE_KQL = """
AppMetrics
| where TimeGenerated >= {start}
| where Name in ("Prompt Tokens", "Prompt Cached Tokens", "Cache Creation Tokens", "Completion Tokens", "Completion Reasoning Tokens")
| extend Dept = tostring(Properties["Department"]), Team0 = tostring(Properties["Team"]),
         User = tostring(Properties["User"]), App = tostring(Properties["ClientApp"]), Model = tostring(Properties["Model"])
| extend Team = iff(isnotempty(Team0), Team0, replace_string(Dept, "dept-", ""))
| summarize Prompt = sumif(Sum, Name == "Prompt Tokens"), Cached = sumif(Sum, Name == "Prompt Cached Tokens"),
            CacheWrite = sumif(Sum, Name == "Cache Creation Tokens"), Output = sumif(Sum, Name == "Completion Tokens"),
            Reasoning = sumif(Sum, Name == "Completion Reasoning Tokens")
    by Day = startofday(TimeGenerated), Team, User, App, Model
"""


def query_usage(start: date) -> list[dict]:
    """Token counts per UTC day / team / person / calling app / model since `start` (UTC day boundary)."""
    ws = common.env("LOG_ANALYTICS_WORKSPACE_ID")
    days = (date.today() - start).days + 2
    r = requests.post(f"https://api.loganalytics.io/v1/workspaces/{ws}/query",
                      headers={"Authorization": f"Bearer {_token('https://api.loganalytics.io')}"},
                      json={"query": USAGE_KQL.format(start=f"datetime({start.isoformat()})"), "timespan": f"P{days}D"},
                      timeout=120)
    r.raise_for_status()
    t = r.json()["tables"][0]
    cols = [c["name"] for c in t["columns"]]
    return [dict(zip(cols, row)) for row in t["rows"]]


def split_tokens(prompt: float, cached: float, cache_write: float, output: float) -> dict[str, float]:
    """Prompt tokens include cached and cache-write tokens; only the rest is billed at the full input price.
    Reasoning tokens are part of the output tokens and billed as output."""
    return {"input": max(prompt - cached - cache_write, 0.0), "cached": cached,
            "cache_write": cache_write, "output": output}


def price_of(entry: dict | None, kind: str) -> float | None:
    """Price per 1M tokens, or None if unknown (missing model, missing or empty value)."""
    v = (entry or {}).get(kind)
    return None if v is None or v == "" else float(v)


def cost_of(tokens: dict[str, float], entry: dict | None) -> tuple[dict[str, float], dict[str, float]]:
    """(cost per token type, unpriced tokens per token type). Tokens without a price are never counted as free."""
    cost, unpriced = {}, {}
    for k in TOKEN_TYPES:
        p = price_of(entry, k)
        if p is None:
            cost[k], unpriced[k] = 0.0, tokens[k]
        else:
            cost[k], unpriced[k] = tokens[k] / 1e6 * p, 0.0
    return cost, unpriced


# ------------------------------------------------------------------------------------------- price table
def load_prices() -> dict:
    data = json.loads(PRICE_FILE.read_text(encoding="utf-8")) if PRICE_FILE.exists() else {}
    data.setdefault("currency", "USD")
    data.setdefault("models", {})
    for m in data["models"].values():
        for k, v in DEFAULT_VARIANT.items():
            m.setdefault(k, v)
        m.setdefault("source", "list")
    return data


def kql_price_block(data: dict) -> str:
    """KQL `prices` datatable + `add_cost` function, wrapped in markers so deployed queries can be updated in place."""
    def lit(v):
        return "real(null)" if v is None else repr(float(v))
    rows = [f'    "{m}", ' + ", ".join(lit(price_of(e, k)) for k in TOKEN_TYPES)
            for m, e in sorted(data["models"].items())]
    return "\n".join([
        f"{KQL_BEGIN} generated from infra/pricing/model_prices.json ({data.get('currency', 'USD')} per 1M tokens) - do not edit",
        "let prices = datatable(Model:string, PIn:real, PCached:real, PWrite:real, POut:real) [",
        ",\n".join(rows) + "];",
        "let maxIn = toscalar(prices | summarize max(PIn)); let maxCached = toscalar(prices | summarize max(PCached));",
        "let maxWrite = toscalar(prices | summarize max(PWrite)); let maxOut = toscalar(prices | summarize max(POut));",
        "// add_cost(conservative): conservative=true prices unknown tokens at the highest known price (enforcement);",
        "// false leaves them out of EstCost and counts them in UnpricedTokens (reporting).",
        "let add_cost = (T:(Model:string, Prompt:real, Cached:real, CacheWrite:real, Output:real), conservative:bool) {",
        "    T",
        "    | lookup kind=leftouter prices on Model",
        "    | extend Uncached = max_of(Prompt - Cached - CacheWrite, 0.0)",
        "    | extend fIn = iff(conservative, coalesce(PIn, maxIn, 0.0), PIn), fCached = iff(conservative, coalesce(PCached, maxCached, maxIn, 0.0), PCached),",
        "             fWrite = iff(conservative, coalesce(PWrite, maxWrite, maxIn, 0.0), PWrite), fOut = iff(conservative, coalesce(POut, maxOut, 0.0), POut)",
        "    | extend EstCost = (iff(Uncached > 0, Uncached * coalesce(fIn, 0.0), 0.0) + iff(Cached > 0, Cached * coalesce(fCached, 0.0), 0.0)",
        "                      + iff(CacheWrite > 0, CacheWrite * coalesce(fWrite, 0.0), 0.0) + iff(Output > 0, Output * coalesce(fOut, 0.0), 0.0)) / 1e6",
        "    | extend UnpricedTokens = iff(isnull(fIn), Uncached, 0.0) + iff(Cached > 0 and isnull(fCached), Cached, 0.0)",
        "                            + iff(CacheWrite > 0 and isnull(fWrite), CacheWrite, 0.0) + iff(isnull(fOut), Output, 0.0)",
        "    | project-away PIn, PCached, PWrite, POut, fIn, fCached, fWrite, fOut, Uncached",
        "};",
        KQL_END,
    ])


def save_prices(data: dict) -> None:
    """Writes the price table and the generated KQL (commit both)."""
    PRICE_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    PRICE_KQL.write_text(kql_price_block(data) + "\n", encoding="utf-8", newline="\n")


def replace_price_block(text: str, block: str) -> tuple[str, int]:
    """Replace the marked price block (also inside JSON-escaped strings)."""
    pattern = re.compile(re.escape(KQL_BEGIN) + r".*?" + re.escape(KQL_END), re.S)
    return pattern.subn(lambda _: block, text)


def alert_query(team: str, data: dict | None = None) -> str:
    """Full spend-alert query for one team - identical to what budget-enforcer.bicep deploys."""
    template = (PRICING_DIR / "team-spend-alert.kql").read_text(encoding="utf-8")
    return kql_price_block(data or load_prices()) + "\n" + template.replace("__TEAM__", team)


def sync_azure(data: dict | None = None) -> list[str]:
    """Push the current price table into the deployed spend alerts (kill switch) and the workbook."""
    data = data or load_prices()
    block = kql_price_block(data)
    done = []
    rules = _arm("GET", f"{_rg_path()}/providers/Microsoft.Insights/scheduledQueryRules").get("value", [])
    for rule in rules:
        if not rule["name"].startswith(ALERT_PREFIX):
            continue
        crit = rule["properties"]["criteria"]["allOf"][0]
        new = alert_query(rule["name"][len(ALERT_PREFIX):], data)
        if crit["query"] != new:
            crit["query"] = new
            body = {"location": rule["location"], "tags": rule.get("tags", {}), "kind": rule.get("kind"),
                    "properties": rule["properties"]}
            _arm("PUT", rule["id"], body)
            done.append(rule["name"])
    wb_id = common.env("WORKBOOK_ID")
    if wb_id:
        wb = _arm("GET", f"{wb_id}?canFetchContent=true", api="2023-06-01")
        old = wb["properties"]["serializedData"]
        wb["properties"]["serializedData"], n = replace_price_block(old, block.replace("\n", "\\n").replace('"', '\\"'))
        if n and wb["properties"]["serializedData"] != old:
            _arm("PUT", wb_id, {"location": wb["location"], "kind": wb.get("kind", "shared"), "tags": wb.get("tags", {}),
                                "properties": {k: wb["properties"][k] for k in ("displayName", "category", "serializedData", "sourceId", "version") if k in wb["properties"]}},
                 api="2023-06-01")
            done.append("workbook")
    return done


# ------------------------------------------------------------------------------------------- list prices
def parse_meter(meter: str) -> dict | None:
    """Meter name -> {model, kind, scope, per, ctx, plan} or None (batch, fine-tuning, other meters)."""
    m = _OLD.match(meter)
    if m and "Batch" not in m["model"] and " ft" not in m["model"]:
        kind = "cached" if m["cached"] else ("input" if m["dir"] == "Inp" else "output")
        return {"model": m["model"], "kind": kind, "scope": m["scope"], "per": 1_000, "ctx": None, "plan": None}
    m = _NEW.match(meter)
    if m:
        kind = {"Cd Inp": "cached", "Cd Wr": "cache_write", "Inp": "input", "Opt": "output"}[m["kind"]]
        return {"model": m["model"], "kind": kind, "scope": m["scope"], "per": 1_000_000, "ctx": m["ctx"], "plan": m["plan"]}
    return None


def meter_model_names(model: str) -> set[str]:
    """Deployment/model name as used in the gateway metric -> model part of meter names.
    gpt-4.1-mini -> 'gpt 4.1 mini'; gpt-6-luna -> '6-luna'."""
    m = model.lower().strip()
    names = {m.replace("-", " ")}
    if m.startswith("gpt-"):
        names.add(m[4:])
    return names


def meter_to_model(meter_model: str, known_models: list[str]) -> str:
    for m in known_models:
        if meter_model.lower() in meter_model_names(m):
            return m
    return meter_model


def fetch_list_price(model: str, region: str, variant: dict, currency: str = "USD") -> tuple[dict, list[str]]:
    """List price per 1M tokens for one model and pricing variant (deployment type, plan, context).
    Returns (prices, notes); token types the model has no meter for are None."""
    scopes = DEPLOYMENT_TYPES[variant["deployment_type"]]
    wanted = meter_model_names(model)
    clauses = " or ".join(f"startswith(meterName, '{n}')" for n in sorted(wanted))
    flt = f"serviceName eq 'Foundry Models' and armRegionName eq '{region}' and type eq 'Consumption' and ({clauses})"
    url, params = RETAIL_API, {"api-version": "2023-01-01-preview", "currencyCode": currency, "$filter": flt}
    found, variants_seen = {}, set()
    while url:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        body = r.json()
        for it in body.get("Items", []):
            p = parse_meter(it["meterName"])
            if not p or p["model"].lower() not in wanted or p["scope"] not in scopes:
                continue
            if p["plan"]:
                variants_seen.add(f"{p['ctx']} {p['plan']}")
                if p["plan"] != PLANS[variant["plan"]] or p["ctx"] != CONTEXTS[variant["context"]]:
                    continue
            found[p["kind"]] = round(float(it["retailPrice"]) * 1_000_000 / p["per"], 6)
        url, params = body.get("NextPageLink"), None
    notes = []
    if not found:
        notes.append(f"{model}: no list price for {variant['deployment_type']} / {variant['plan']} / {variant['context']} context")
    elif len({v.split()[0] for v in variants_seen}) > 1:
        notes.append(f"{model}: billed differently for short and long context - priced as {variant['context']} context; "
                     "gateway metrics don't show context length")
    return {k: found.get(k) for k in TOKEN_TYPES}, notes


def refresh_list_prices(data: dict, region: str) -> tuple[dict, list[str]]:
    """Refresh every model whose source is 'list'; models marked 'manual' (negotiated prices) are kept."""
    notes = []
    for model, entry in data["models"].items():
        if entry.get("source") == "manual":
            continue
        prices, n = fetch_list_price(model, region, entry, data.get("currency", "USD"))
        notes += n
        if any(v is not None for v in prices.values()):
            entry.update(prices)
    data["region"] = region
    data["updated"] = date.today().isoformat()
    return data, notes


# ------------------------------------------------------------------------------------------- actual cost
def query_actual_cost(start: date, end: date) -> tuple[list[dict], str]:
    """Actual cost per model meter in the PoC resource group (Cost Management, 8-24 h delay).
    Returns rows {meter, model, kind, cost} and the billing currency."""
    body = {
        "type": "ActualCost", "timeframe": "Custom",
        "timePeriod": {"from": start.isoformat(), "to": end.isoformat()},
        "dataset": {"granularity": "None", "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                    "grouping": [{"type": "Dimension", "name": "Meter"}],
                    "filter": {"dimensions": {"name": "ServiceName", "operator": "In",
                                              "values": ["Foundry Models", "Azure OpenAI", "Cognitive Services"]}}},
    }
    props = _arm("POST", f"{_rg_path()}/providers/Microsoft.CostManagement/query", body, api="2023-11-01")["properties"]
    cols = [c["name"] for c in props["columns"]]
    rows, currency = [], "USD"
    for row in props["rows"]:
        d = dict(zip(cols, row))
        currency = d.get("Currency", currency)
        p = parse_meter(d["Meter"])
        rows.append({"meter": d["Meter"], "model": p["model"] if p else "(other)",
                     "kind": p["kind"] if p else "other", "cost": float(d["Cost"])})
    return rows, currency
