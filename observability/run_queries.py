"""Run the queries in queries.kql against the Log Analytics workspace and print tables.

  python run_queries.py --list
  python run_queries.py [--only tokens_per_dept] [--hours 24]

Needs LOG_ANALYTICS_WORKSPACE_ID in .env (workspace customer-id GUID; a full ARM resource id is
resolved via `az monitor log-analytics workspace show`).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parent / ".env"
GUID = re.compile(r"^[0-9a-fA-F-]{36}$")
console = Console()


def load_queries() -> dict[str, str]:
    queries: dict[str, str] = {}
    name = None
    for line in (HERE / "queries.kql").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^// ### (\w+)", line)
        if m:
            name = m.group(1)
            queries[name] = ""
        elif name and not line.startswith("//"):
            queries[name] += line + "\n"
    return {k: v.strip() for k, v in queries.items() if v.strip()}


def workspace_guid(value: str) -> str:
    if GUID.match(value):
        return value
    if value.lower().startswith("/subscriptions/"):
        parts = value.split("/")
        out = subprocess.run(
            ["az", "monitor", "log-analytics", "workspace", "show", "--ids", value,
             "--query", "customerId", "-o", "tsv"], capture_output=True, text=True, shell=(os.name == "nt"))
        if out.returncode == 0 and GUID.match(out.stdout.strip()):
            return out.stdout.strip()
        sys.exit(f"ERROR: could not resolve customerId for workspace '{parts[-1]}': {out.stderr.strip()[:200]}")
    sys.exit("ERROR: LOG_ANALYTICS_WORKSPACE_ID must be a workspace GUID or a full resource id.")


def run(client, ws: str, name: str, kql: str, hours: int) -> bool:
    from azure.monitor.query import LogsQueryStatus
    kql = kql.replace("ago(24h)", f"ago({hours}h)")
    try:
        res = client.query_workspace(ws, kql, timespan=timedelta(hours=hours))
    except Exception as e:
        console.print(f"[red]{name}: {type(e).__name__}: {str(e)[:300]}[/red]")
        return False
    tables = res.tables if res.status == LogsQueryStatus.SUCCESS else res.partial_data
    t = Table(title=name)
    if tables:
        for c in tables[0].columns:
            t.add_column(str(c))
        for row in tables[0].rows[:25]:
            t.add_row(*[str(v) for v in row])
    console.print(t)
    console.print(f"[dim]{len(tables[0].rows) if tables else 0} rows[/dim]\n")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true")
    p.add_argument("--only", action="append", help="query name (repeatable)")
    p.add_argument("--hours", type=int, default=24)
    a = p.parse_args()
    queries = load_queries()
    queries.pop("request_lookup", None)  # needs a manual GUID
    if a.list:
        print("\n".join(queries))
        return 0
    if not ENV_FILE.exists():
        sys.stderr.write(f"ERROR: {ENV_FILE} not found. Run scripts\\deploy.ps1 first.\n")
        return 2
    load_dotenv(ENV_FILE)
    raw = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID", "")
    if not raw:
        sys.stderr.write("ERROR: LOG_ANALYTICS_WORKSPACE_ID missing in .env\n")
        return 2
    from azure.identity import DefaultAzureCredential
    from azure.monitor.query import LogsQueryClient
    ws = workspace_guid(raw)
    client = LogsQueryClient(DefaultAzureCredential())
    names = a.only or list(queries)
    unknown = [n for n in names if n not in queries]
    if unknown:
        sys.stderr.write(f"Unknown query: {unknown}. Use --list.\n")
        return 2
    ok = [run(client, ws, n, queries[n], a.hours) for n in names]
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
