"""EUR budget -> hard stop demo (Logic App budget enforcer flips APIM named value team-<team>-enabled).

  python budget_demo.py --mode status
  python budget_demo.py --team cs --mode simulate [--no-reset]   # alert payload -> Logic App -> 403 -> reset -> 200
  python budget_demo.py --team cs --mode reset                    # re-enable via la-portops-budget-reset (the enforcer only disables)

The Logic App callback URLs (contain a SAS signature) is fetched at runtime and never stored.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests
from rich.console import Console
from rich.table import Table

import common

console = Console()
ARM = "https://management.azure.com"
ENFORCER = "la-portops-budget-enforcer"
RESET = "la-portops-budget-reset"
ENFORCER_TRIGGER = "When_a_budget_alert_or_manual_request_is_received"
RESET_TRIGGER = "Manual_reenable"


def _az(*args: str) -> str:
    az = "az.cmd" if sys.platform == "win32" else "az"
    r = subprocess.run([az, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"az {' '.join(args[:3])} failed: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def _token(resource: str) -> str:
    return _az("account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv")


def _base() -> str:
    return (f"{ARM}/subscriptions/{common.env('AZURE_SUBSCRIPTION_ID')}/resourceGroups/{common.env('AZURE_RESOURCE_GROUP')}")


def named_value(team: str) -> str:
    url = (f"{_base()}/providers/Microsoft.ApiManagement/service/{common.env('APIM_NAME')}"
           f"/namedValues/team-{team}-enabled?api-version=2024-05-01")
    r = requests.get(url, headers={"Authorization": f"Bearer {_token(ARM)}"}, timeout=60)
    r.raise_for_status()
    return str(r.json()["properties"]["value"]).lower()


def callback_url(workflow: str = ENFORCER, trigger: str = ENFORCER_TRIGGER) -> str:
    url = (f"{_base()}/providers/Microsoft.Logic/workflows/{workflow}/triggers/"
           f"{trigger}/listCallbackUrl?api-version=2019-05-01")
    r = requests.post(url, headers={"Authorization": f"Bearer {_token(ARM)}"}, timeout=60)
    r.raise_for_status()
    return r.json()["value"]


def last_run() -> str:
    url = f"{_base()}/providers/Microsoft.Logic/workflows/{ENFORCER}/runs?api-version=2019-05-01&$top=1"
    r = requests.get(url, headers={"Authorization": f"Bearer {_token(ARM)}"}, timeout=60)
    r.raise_for_status()
    v = r.json().get("value", [])
    if not v:
        return "no runs"
    p = v[0]["properties"]
    return f"{v[0]['name']} status={p['status']} start={p['startTime']}"


def todays_estimate() -> dict[str, float]:
    """Estimated USD per team since midnight UTC (same maths as the scheduled query alerts)."""
    prices = ", ".join(f'"{m}", {i}, {o}' for m, (i, o) in common.PRICES.items())
    q = f"""
let prices = datatable(Model:string, PriceIn:real, PriceOut:real) [{prices}];
AppMetrics
| where TimeGenerated >= startofday(now())
| where Name in ("Prompt Tokens", "Completion Tokens")
| extend Team = iff(tostring(Properties.Team) != "", tostring(Properties.Team), replace_string(tostring(Properties.Department), "dept-", "")), Model = tostring(Properties.Model)
| lookup prices on Model
| extend Price = iff(Name == "Prompt Tokens", coalesce(PriceIn, 0.40), coalesce(PriceOut, 1.60))
| summarize EstimatedUsd = sum(Sum * Price / 1000000.0) by Team
"""
    r = requests.post(f"https://api.loganalytics.io/v1/workspaces/{common.env('LOG_ANALYTICS_WORKSPACE_ID')}/query",
                      json={"query": q}, headers={"Authorization": f"Bearer {_token('https://api.loganalytics.io')}"}, timeout=60)
    r.raise_for_status()
    return {row[0]: float(row[1]) for row in r.json()["tables"][0]["rows"]}


def gateway_status(team: str) -> tuple[int, str]:
    url = (f"{common.env('APIM_GATEWAY_URL').rstrip('/')}/openai/deployments/"
           f"{common.env('MODEL_SMALL', 'gpt-4.1-mini')}/chat/completions?api-version={common.API_VERSION}")
    r = requests.post(url, timeout=60, headers={"api-key": common.env(f"APIM_KEY_{team.upper()}")},
                      json={"messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 10})
    code = ""
    if r.status_code == 403:
        try:
            code = r.json()["error"]["code"]
        except Exception:
            code = r.text[:60]
    return r.status_code, code


def wait_named_value(team: str, expected: str, timeout: int = 120) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if named_value(team) == expected:
            return True
        time.sleep(3)
    return False


def wait_gateway(team: str, expected: int, timeout: int = 120) -> tuple[int, str]:
    """Named value changes reach the gateway after a short cache delay."""
    end = time.time() + timeout
    status = gateway_status(team)
    while status[0] != expected and time.time() < end:
        time.sleep(4)
        status = gateway_status(team)
    return status


def alert_payload(team: str) -> dict:
    """Realistic Azure Monitor common alert schema payload of a scheduled query alert."""
    sub, rg = common.env("AZURE_SUBSCRIPTION_ID"), common.env("AZURE_RESOURCE_GROUP")
    rule = f"alert-portops-spend-{team}"
    return {
        "schemaId": "azureMonitorCommonAlertSchema",
        "data": {
            "essentials": {
                "alertId": f"/subscriptions/{sub}/providers/Microsoft.AlertsManagement/alerts/simulated-{int(time.time())}",
                "alertRule": rule,
                "severity": "Sev4",
                "signalType": "Log",
                "monitorCondition": "Fired",
                "monitoringService": "Log Alerts V2",
                "alertTargetIDs": [f"/subscriptions/{sub}/resourcegroups/{rg}/providers/microsoft.operationalinsights/workspaces/log-portops"],
                "firedDateTime": datetime.now(timezone.utc).isoformat(),
                "description": f"SIMULATED: estimated spend today for team {team} above threshold",
            },
            "alertContext": {"conditionType": "SingleResourceMultipleMetricCriteria"},
            "customProperties": {"team": team, "thresholdUsd": "simulated"},
        },
    }


def show_status() -> None:
    est = todays_estimate()
    t = Table(title="Team kill switch + estimated spend today (UTC)")
    for c in ("team", "team-<x>-enabled", "est. USD today"):
        t.add_column(c)
    for team in common.DEPARTMENTS:
        t.add_row(team, named_value(team), f"{est.get(team, 0.0):.4f}")
    console.print(t)
    console.print(f"Last enforcer run: {last_run()}")


def do_reset(team: str) -> bool:
    r = requests.post(callback_url(RESET, RESET_TRIGGER), json={"team": team}, timeout=120)
    console.print(f"reset workflow(re-enable {team}) -> HTTP {r.status_code}")
    ok = r.status_code in (200, 202) and wait_named_value(team, "true")
    status = wait_gateway(team, 200)
    console.print(f"gateway call after reset -> {status[0]}")
    return ok and status[0] == 200


def do_simulate(team: str, no_reset: bool) -> bool:
    ok = False
    try:
        before = named_value(team)
        console.print(f"team-{team}-enabled before: {before}")
        r = requests.post(callback_url(), json=alert_payload(team), timeout=120)
        console.print(f"POST common-alert-schema payload -> Logic App: HTTP {r.status_code} {r.text[:200]}")
        console.print(f"Logic App run: {last_run()}")
        flipped = r.status_code == 200 and wait_named_value(team, "false")
        console.print(f"team-{team}-enabled after: {named_value(team)}")
        status = wait_gateway(team, 403) if flipped else gateway_status(team)
        console.print(f"gateway call for team {team}: {status[0]} {status[1]}")
        ok = flipped and status == (403, "TeamBudgetExhausted") and "Succeeded" in last_run()
    finally:
        if not no_reset:
            console.print("[bold]reset[/bold]")
            ok = do_reset(team) and ok
    console.print(f"[green]PASS[/green]" if ok else "[red]FAIL[/red]")
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--team", choices=common.DEPARTMENTS, default="cs")
    p.add_argument("--mode", choices=("simulate", "reset", "status"), default="status")
    p.add_argument("--no-reset", action="store_true", help="simulate: leave the team disabled")
    a = p.parse_args()
    common.load_env(("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "APIM_NAME", "APIM_GATEWAY_URL",
                     "LOG_ANALYTICS_WORKSPACE_ID", f"APIM_KEY_{a.team.upper()}"))
    if a.mode == "status":
        show_status()
        return 0
    if a.mode == "reset":
        return 0 if do_reset(a.team) else 1
    return 0 if do_simulate(a.team, a.no_reset) else 1


if __name__ == "__main__":
    sys.exit(main())
