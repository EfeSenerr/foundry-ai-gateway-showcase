"""Terminal Ops Assistant - CLI agent whose LLM traffic goes through the APIM AI Gateway.

Per-department scope: the department key selects the APIM product (token quota) and the
department-scoped MCP endpoint (/<dept>/mcp) selects which tools the agent may see.

  python agent_via_gateway.py --dept ops "Which vessels arrive today and which berth?"
  python agent_via_gateway.py --dept it --model auto          # interactive loop
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import common

console = Console()

SYSTEM_PROMPTS = {
    "ops": "You are the Terminal Ops Assistant for Northport Container Terminal (terminal operations). "
           "Use the tools for vessel, berth, crane and container data. Be concise; never invent data.",
    "it": "You are the Terminal IT Assistant for Northport Container Terminal. Use the incident and log "
          "tools to analyse errors. Be concise; never invent data.",
    "cs": "You are the Customer Service Assistant for Northport Container Terminal. Use the container "
          "and truck-slot tools to answer customers. Be concise; never invent data.",
}


@asynccontextmanager
async def mcp_session(url: str, headers: dict | None = None):
    """MCP session; for APIM gateway URLs the team key is added automatically (common.mcp_headers_for)."""
    from mcp import ClientSession
    async with common.mcp_client(url, headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def to_openai_tools(mcp_tools) -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description or "",
            # mcp SDK 1.x uses inputSchema, 2.x uses input_schema
            "parameters": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
            or {"type": "object", "properties": {}},
        },
    } for t in mcp_tools]


def show_headers(headers) -> None:
    table = Table(title="Gateway response headers", show_header=False)
    for k, v in common.pick_headers(headers).items():
        table.add_row(k, v)
    console.print(table)


async def ask(client, session, tools, model: str, messages: list[dict], max_rounds: int = 6):
    """Run the tool loop. Returns the headers of the last gateway response."""
    last_headers = {}
    for _ in range(max_rounds):
        raw = await asyncio.to_thread(
            client.chat.completions.with_raw_response.create,
            model=model, messages=messages, tools=tools or None)
        last_headers = raw.headers
        resp = raw.parse()
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            console.print(Panel(msg.content or "", title="Answer", border_style="green"))
            if resp.usage:
                console.print(f"[dim]tokens: prompt={resp.usage.prompt_tokens} "
                              f"completion={resp.usage.completion_tokens}[/dim]")
            return last_headers
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            console.print(f"[cyan]tool call[/cyan] {tc.function.name}({json.dumps(args)})")
            result = await session.call_tool(tc.function.name, args)
            text = "\n".join(getattr(c, "text", str(c)) for c in result.content)
            console.print(f"[dim]  -> {text[:300]}{'...' if len(text) > 300 else ''}[/dim]")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
    console.print("[yellow]max tool rounds reached[/yellow]")
    return last_headers


async def run_agent(dept: str, question: str, model: str = "gpt-4.1-mini", max_rounds: int = 6) -> dict:
    """Reusable (UI) entry point. Returns answer, tool_calls, headers, usage, tools_visible.

    Raises openai.APIStatusError (e.g. 429) so callers can show gateway headers.
    """
    client = common.gateway_client(dept)
    out = {"answer": "", "tool_calls": [], "headers": {}, "usage": {"prompt": 0, "completion": 0},
           "tools_visible": [], "model": model}
    async with mcp_session(common.dept_mcp_url(dept)) as session:
        mcp_tools = (await session.list_tools()).tools
        out["tools_visible"] = [t.name for t in mcp_tools]
        tools = to_openai_tools(mcp_tools)
        messages = [{"role": "system", "content": SYSTEM_PROMPTS[dept]},
                    {"role": "user", "content": question}]
        for _ in range(max_rounds):
            raw = await asyncio.to_thread(
                client.chat.completions.with_raw_response.create,
                model=model, messages=messages, tools=tools or None)
            out["headers"] = common.pick_headers(raw.headers)
            resp = raw.parse()
            if resp.usage:
                out["usage"]["prompt"] += resp.usage.prompt_tokens
                out["usage"]["completion"] += resp.usage.completion_tokens
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                out["answer"] = msg.content or ""
                return out
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                try:
                    result = await session.call_tool(tc.function.name, args)
                    text = "\n".join(getattr(c, "text", str(c)) for c in result.content)
                except Exception as e:
                    text = f"tool error: {type(e).__name__}: {str(e)[:200]}"
                out["tool_calls"].append({"name": tc.function.name, "args": args, "result": text[:600]})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
    out["answer"] = "(max tool rounds reached)"
    return out

async def main(args) -> int:
    from openai import APIStatusError
    client = common.gateway_client(args.dept)
    url = common.dept_mcp_url(args.dept)
    console.print(f"[bold]Dept[/bold] {args.dept} | [bold]model[/bold] {args.model} | "
                  f"[bold]gateway[/bold] {common.env('APIM_GATEWAY_URL')} | [bold]MCP[/bold] {url}")
    async with mcp_session(url) as session:
        mcp_tools = (await session.list_tools()).tools
        console.print(f"Tools visible to '{args.dept}': {', '.join(t.name for t in mcp_tools)}")
        tools = to_openai_tools(mcp_tools)

        questions = [args.question] if args.question else None
        while True:
            if questions is not None:
                if not questions:
                    break
                q = questions.pop(0)
            else:
                try:
                    q = console.input("[bold magenta]you> [/bold magenta]").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if q in ("", "exit", "quit"):
                    break
            messages = [{"role": "system", "content": SYSTEM_PROMPTS[args.dept]},
                        {"role": "user", "content": q}]
            try:
                headers = await ask(client, session, tools, args.model, messages)
                show_headers(headers)
            except APIStatusError as e:
                console.print(f"[red]Gateway returned {e.status_code}[/red]: {str(e.message)[:300]}")
                show_headers(e.response.headers)
                if questions is not None:
                    return 1
    return 0


def parse():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dept", choices=common.DEPARTMENTS, default="ops")
    p.add_argument("--model", default="gpt-4.1-mini",
                   help="APIM deployment name; 'auto' lets the gateway route (default: gpt-4.1-mini)")
    p.add_argument("question", nargs="?", help="question; omit for an interactive loop")
    return p.parse_args()


if __name__ == "__main__":
    a = parse()
    common.load_env(("APIM_GATEWAY_URL", f"APIM_KEY_{a.dept.upper()}", "MCP_URL"))
    sys.exit(asyncio.run(main(a)))
