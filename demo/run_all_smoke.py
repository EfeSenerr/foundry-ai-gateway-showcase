"""Non-interactive end-to-end smoke test: runs every demo check and prints a PASS/FAIL table.

  python run_all_smoke.py [--skip-agents] [--skip-kql] [--skip-entra] [--skip-budget] [--skip-mcp-gov]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

import common

console = Console()
HERE = Path(__file__).resolve().parent


def sh(args: list[str], timeout: int = 240) -> tuple[bool, str]:
    try:
        r = subprocess.run([sys.executable, *args], cwd=HERE, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, "timeout"
    out = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0, (out[-1] if out else "")[:100]


def gateway_basic(dept: str) -> tuple[bool, str]:
    try:
        raw = common.gateway_client(dept).chat.completions.with_raw_response.create(
            model=common.env("MODEL_SMALL", "gpt-4.1-mini"), max_tokens=20,
            messages=[{"role": "user", "content": "Reply with: ok"}])
        h = raw.headers
        return raw.status_code == 200, f"dept hdr={common.header(h, 'x-portops-department')} id={common.header(h, 'apim-request-id')}"
    except Exception as e:
        return False, f"{getattr(e, 'status_code', 'ERR')} {str(e)[:80]}"


def mcp_tools(dept: str) -> tuple[bool, str]:
    import asyncio
    from agent_via_gateway import mcp_session

    async def go():
        async with mcp_session(common.dept_mcp_url(dept)) as s:
            return [t.name for t in (await s.list_tools()).tools]
    try:
        names = asyncio.run(go())
        expected = set(common.DEPARTMENT_TOOLS[dept])
        return set(names) == expected, f"{len(names)} tools" + ("" if set(names) == expected else f" (expected {sorted(expected)})")
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"


def _entra_url() -> str:
    return (f"{common.env('APIM_GATEWAY_URL').rstrip('/')}/entra/openai/deployments/"
            f"{common.env('MODEL_SMALL', 'gpt-4.1-mini')}/chat/completions?api-version={common.API_VERSION}")


_BODY = {"messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 10}


def entra_no_token() -> tuple[bool, str]:
    import requests
    r = requests.post(_entra_url(), json=_BODY, timeout=60)
    return r.status_code == 401, f"status={r.status_code}"


def entra_admin() -> tuple[bool, str]:
    import requests
    from entra_limits_demo import token_me
    r = requests.post(_entra_url(), json=_BODY, timeout=60, headers={"Authorization": f"Bearer {token_me()}"})
    team = r.headers.get("x-portops-team", "")
    return r.status_code == 200 and bool(team), f"status={r.status_code} team={team} user_quota_left={r.headers.get('x-portops-user-remaining-quota', '')}"


def _set_named_value(name: str, value: str) -> None:
    import requests
    az = "az.cmd" if sys.platform == "win32" else "az"
    tok = subprocess.run([az, "account", "get-access-token", "--resource", "https://management.azure.com",
                          "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True).stdout.strip()
    sub, rg, apim = (common.env(k) for k in ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "APIM_NAME"))
    url = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ApiManagement/"
           f"service/{apim}/namedValues/{name}?api-version=2024-05-01")
    r = requests.patch(url, json={"properties": {"value": value}}, headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    r.raise_for_status()


def team_kill_switch() -> tuple[bool, str]:
    """team-cs-enabled=false must block the key-based cs API (403 TeamBudgetExhausted); always restored."""
    import time
    import requests
    url = (f"{common.env('APIM_GATEWAY_URL').rstrip('/')}/openai/deployments/"
           f"{common.env('MODEL_SMALL', 'gpt-4.1-mini')}/chat/completions?api-version={common.API_VERSION}")

    def call():
        return requests.post(url, json=_BODY, timeout=60, headers={"api-key": common.env("APIM_KEY_CS")})
    try:
        _set_named_value("team-cs-enabled", "false")
        r = call()
        for _ in range(20):
            if r.status_code == 403:
                break
            time.sleep(4)
            r = call()
        blocked = r.status_code == 403 and "TeamBudgetExhausted" in r.text
    finally:
        _set_named_value("team-cs-enabled", "true")
    after = call()
    for _ in range(20):
        if after.status_code == 200:
            break
        time.sleep(4)
        after = call()
    return blocked and after.status_code == 200, f"disabled={r.status_code} restored={after.status_code}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-agents", action="store_true")
    p.add_argument("--skip-kql", action="store_true")
    p.add_argument("--skip-entra", action="store_true")
    p.add_argument("--skip-budget", action="store_true")
    p.add_argument("--skip-mcp-gov", action="store_true", help="skip the MCP gateway governance checks")
    a = p.parse_args()
    common.load_env(("APIM_GATEWAY_URL", "APIM_KEY_OPS", "APIM_KEY_IT", "APIM_KEY_CS", "MCP_URL"))

    checks: list[tuple[str, tuple[bool, str]]] = []
    for d in common.DEPARTMENTS:
        checks.append((f"gateway chat ({d})", gateway_basic(d)))
    for d in common.DEPARTMENTS:
        checks.append((f"MCP scoped tools ({d})", mcp_tools(d)))
    if not a.skip_mcp_gov and common.env("MCP_GATEWAY_URL"):
        try:
            from mcp_governance_demo import run as mcp_gov_run
            for name, ok, detail in mcp_gov_run():
                checks.append((f"MCP gov {name}", (ok, detail[:100])))
        except Exception as e:
            checks.append(("MCP gov", (False, f"{type(e).__name__}: {str(e)[:80]}")))
    for d, q in (("ops", "Which vessels arrive soon?"), ("it", "Summarize recent errors."),
                 ("cs", "List available truck slots.")):
        checks.append((f"agent via gateway ({d})", sh(["agent_via_gateway.py", "--dept", d, q])))
    checks.append(("token limit 429 (it)", sh(["token_limit_demo.py", "--no-compare"])))
    checks.append(("chaos 500", sh(["chaos_500_demo.py"])))
    checks.append(("model routing", sh(["model_routing_demo.py"])))
    if not a.skip_entra and common.env("ENTRA_API_AUDIENCE"):
        for name, fn in (("entra: no token -> 401", entra_no_token),
                         ("entra: admin token -> 200 + team header", entra_admin),
                         ("team kill switch -> 403 (+restore)", team_kill_switch)):
            try:
                checks.append((name, fn()))
            except Exception as e:
                checks.append((name, (False, f"{type(e).__name__}: {str(e)[:80]}")))
    if not a.skip_budget:
        try:
            checks.append(("budget enforcer simulate -> 403 -> reset", sh(["budget_demo.py", "--team", "cs", "--mode", "simulate"], 600)))
        finally:
            sh(["budget_demo.py", "--team", "cs", "--mode", "reset"], 300)
    if not a.skip_agents:
        # Foundry agents now call models via the gateway; the 'it' product (2000 TPM) is still
        # drained by the token-limit burst above, so let the token bucket refill first.
        time.sleep(65)
        checks.append(("foundry agents create+test", sh(["create_foundry_agents.py", "--test"], 600)))
    if not a.skip_kql and common.env("LOG_ANALYTICS_WORKSPACE_ID"):
        checks.append(("KQL queries", sh(["../observability/run_queries.py", "--only", "tokens_per_dept"], 120)))

    t = Table(title="Smoke test")
    for c in ("check", "result", "detail"):
        t.add_column(c)
    for name, (ok, detail) in checks:
        t.add_row(name, "[green]PASS[/green]" if ok else "[red]FAIL[/red]", detail)
    console.print(t)
    return 0 if all(ok for _, (ok, _) in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
