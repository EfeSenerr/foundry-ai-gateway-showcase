"""Chaos demo: a normal request vs. one with 'x-chaos: 500', then how to find both in Log Analytics.

  python chaos_500_demo.py [--dept ops]
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.syntax import Syntax

import common

console = Console()


def call(dept: str, model: str, chaos: bool) -> tuple[int, str]:
    client = common.gateway_client(dept)
    extra = {"x-chaos": "500"} if chaos else None
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=model, max_tokens=30, extra_headers=extra,
            messages=[{"role": "user", "content": "Say 'gateway ok' in two words."}])
        return raw.status_code, common.header(raw.headers, "apim-request-id")
    except Exception as e:
        resp = getattr(e, "response", None)
        rid = common.header(resp.headers, "apim-request-id") if resp is not None else ""
        return getattr(e, "status_code", 0), rid


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dept", choices=common.DEPARTMENTS, default="ops")
    a = p.parse_args()
    common.load_env(("APIM_GATEWAY_URL", f"APIM_KEY_{a.dept.upper()}"))
    model = common.env("MODEL_SMALL", "gpt-4.1-mini")

    ok_status, ok_id = call(a.dept, model, False)
    bad_status, bad_id = call(a.dept, model, True)
    console.print(f"normal request : status={ok_status} apim-request-id={ok_id}")
    console.print(f"chaos  request : status={bad_status} apim-request-id={bad_id}")

    kql = f"""ApiManagementGatewayLogs
| where TimeGenerated > ago(1h)
| where CorrelationId in ("{ok_id}", "{bad_id}")
| project TimeGenerated, CorrelationId, ResponseCode, BackendResponseCode, TotalTime, BackendTime, Url
// ResponseCode=500 with empty BackendResponseCode => error produced by the gateway, not by Foundry"""
    console.print(Syntax(kql, "kusto"))
    console.print("[dim]Logs can take 2-5 minutes to appear in Log Analytics.[/dim]")
    passed = ok_status == 200 and bad_status == 500
    console.print("[green]PASS[/green]" if passed else "[red]FAIL[/red]: expected 200 and 500")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
