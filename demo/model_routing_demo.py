"""Model-routing demo: send a short and a long 'analyze...' question to deployment 'auto'.

The gateway picks the model and reports it in the x-portops-routed-model header.

  python model_routing_demo.py
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

import common

console = Console()

QUESTIONS = {
    "short": "What is a TEU?",
    "long": ("Analyze the trade-offs between berth-window prioritisation and yard-crane utilisation "
             "for a terminal handling 3 vessels arriving within 6 hours. Compare at least three "
             "strategies, discuss risks for customs-held containers and recommend one with reasoning."),
}


def route(dept: str, name: str, q: str) -> dict:
    """One 'auto' call. Returns routed model, tokens, cost, request id (raises on gateway errors)."""
    raw = common.gateway_client(dept).chat.completions.with_raw_response.create(
        model="auto", max_tokens=400, messages=[{"role": "user", "content": q}])
    r = raw.parse()
    model = common.header(raw.headers, "x-portops-routed-model") or r.model or "?"
    base = next((m for m in common.PRICES if str(model).startswith(m)), "gpt-4.1-mini")
    return {"name": name, "model": str(model), "prompt": r.usage.prompt_tokens,
            "completion": r.usage.completion_tokens,
            "cost": common.estimate_cost(base, r.usage.prompt_tokens, r.usage.completion_tokens),
            "request_id": common.header(raw.headers, "apim-request-id")}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dept", choices=common.DEPARTMENTS, default="ops")
    a = p.parse_args()
    common.load_env(("APIM_GATEWAY_URL", f"APIM_KEY_{a.dept.upper()}"))
    client = common.gateway_client(a.dept)

    t = Table(title="Model routing via deployment 'auto' (cost: illustrative list price, verify)")
    for c in ("question", "routed model", "prompt tok", "completion tok", "est. cost USD", "apim-request-id"):
        t.add_column(c)
    routed = {}
    for name, q in QUESTIONS.items():
        try:
            raw = client.chat.completions.with_raw_response.create(
                model="auto", max_tokens=400, messages=[{"role": "user", "content": q}])
        except Exception as e:
            console.print(f"[red]{name}: {getattr(e, 'status_code', 'ERR')} {str(e)[:200]}[/red]")
            return 1
        r = raw.parse()
        model = common.header(raw.headers, "x-portops-routed-model") or r.model or "?"
        routed[name] = model
        base = next((m for m in common.PRICES if str(model).startswith(m)), "gpt-4.1-mini")
        cost = common.estimate_cost(base, r.usage.prompt_tokens, r.usage.completion_tokens)
        t.add_row(name, str(model), str(r.usage.prompt_tokens), str(r.usage.completion_tokens),
                  f"{cost:.6f}", common.header(raw.headers, "apim-request-id"))
    console.print(t)
    ok = routed["short"] != routed["long"]
    console.print("[green]PASS[/green]: different models routed" if ok else
                  "[yellow]WARN[/yellow]: same model for both questions")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
