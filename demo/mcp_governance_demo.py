"""MCP governance demo: the MCP server is only reachable through the APIM gateway, per team.

  a) ops key lists tools via /mcp/ops and calls an allowed tool           -> OK
  b) cs key calls an ops-only tool (get_berth_plan)                        -> blocked by the gateway allow-list
  c) it key on the /mcp/ops path                                           -> 403 (team != path)
  d) direct call to the Container App without / with a wrong x-mcp-key     -> rejected (401)
  e) KQL to see the McpToolCalls metric per team / tool / result

  python mcp_governance_demo.py [--json]
"""
from __future__ import annotations

import asyncio
import json
import sys

import requests
from rich.console import Console
from rich.table import Table

import common

console = Console()
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "governance-demo", "version": "1"}}}
HDRS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

KQL = """\
AppMetrics
| where Name == "McpToolCalls"
| extend Team = tostring(Properties.Team), Tool = tostring(Properties.Tool), Result = tostring(Properties.Result)
| summarize Calls = sum(Sum) by Team, Tool, Result
| order by Team asc, Tool asc"""


async def call(team: str, tool: str, args: dict, path_team: str | None = None) -> tuple[str, str]:
    """Return (outcome, detail) for list_tools + call_tool via the gateway as `team`."""
    from mcp import ClientSession
    url = common.gateway_mcp_url(path_team or team)
    headers = {"api-key": common.env(f"MCP_KEY_{team.upper()}")}
    async with common.mcp_client(url, headers) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            try:
                res = await s.call_tool(tool, args)
            except Exception as e:  # JSON-RPC error: McpError in mcp 1.x, MCPError in 2.x
                if "not permitted" not in str(e):
                    raise
                return "blocked", f"{len(tools)} tools listed; JSON-RPC error: {str(e)[:110]}"
            if getattr(res, "is_error", None) or getattr(res, "isError", False):
                return "tool-error", str(res.content)[:110]
            text = "\n".join(getattr(c, "text", str(c)) for c in res.content)
            return "ok", f"{len(tools)} tools listed; {tool} -> {' '.join(text.split())[:70]}..."


def http_post(url: str, extra: dict) -> requests.Response:
    return requests.post(url, json=INIT, headers={**HDRS, **extra}, timeout=60)


def run() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    try:
        o, d = asyncio.run(call("ops", "get_berth_plan", {"berth": "A1"}))
        out.append(("a) ops key: list + allowed tool via /mcp/ops", o == "ok", f"{o}: {d}"))
    except Exception as e:
        out.append(("a) ops key: list + allowed tool via /mcp/ops", False, f"{type(e).__name__}: {str(e)[:100]}"))

    try:
        o, d = asyncio.run(call("cs", "get_berth_plan", {"berth": "A1"}))
        out.append(("b) cs key calls ops-only tool get_berth_plan", o == "blocked", f"{o}: {d}"))
    except Exception as e:
        out.append(("b) cs key calls ops-only tool get_berth_plan", False, f"{type(e).__name__}: {str(e)[:100]}"))

    r = http_post(common.gateway_mcp_url("ops"), {"api-key": common.env("MCP_KEY_IT")})
    out.append(("c) it key on /mcp/ops path", r.status_code == 403,
                f"HTTP {r.status_code} {r.text[:100]}"))

    direct = common.direct_mcp_url("ops")
    r1 = http_post(direct, {})
    r2 = http_post(direct, {"x-mcp-key": "wrong-key"})
    out.append(("d) direct Container App call without / wrong x-mcp-key",
                r1.status_code in (401, 403) and r2.status_code in (401, 403),
                f"no key HTTP {r1.status_code}, wrong key HTTP {r2.status_code}"))
    return out


def main() -> int:
    common.load_env(("MCP_URL", "MCP_GATEWAY_URL", "MCP_KEY_OPS", "MCP_KEY_IT", "MCP_KEY_CS"))
    results = run()
    t = Table(title="MCP governance through the APIM gateway")
    for c in ("scenario", "expected", "detail"):
        t.add_column(c)
    for name, ok, detail in results:
        t.add_row(name, "[green]PASS[/green]" if ok else "[red]FAIL[/red]", detail)
    console.print(t)
    console.print("\n[bold]e) See the calls per team/tool (Log Analytics, ~3-5 min after the calls):[/bold]")
    console.print(KQL)
    console.print("\nChange a team's tools live: APIM > Named values > mcp-tools-<team> (comma list).")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
