"""Token-limit demo: hammer the gateway with the low-TPM 'it' key until 429, compare with 'ops'.

  python token_limit_demo.py [--requests 12] [--parallel 6]
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.table import Table

import common

console = Console()
PROMPT = ("Write a detailed 300-word explanation of how a container terminal plans berth "
          "allocation and crane assignment for a large vessel.")


def one(dept: str, i: int, model: str) -> dict:
    client = common.gateway_client(dept)
    t0 = time.time()
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=model, max_tokens=400,
            messages=[{"role": "user", "content": PROMPT}])
        h = raw.headers
        r = raw.parse()
        return {"dept": dept, "i": i, "status": 200, "headers": h,
                "tokens": r.usage.total_tokens if r.usage else "", "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # APIStatusError carries status + headers
        resp = getattr(e, "response", None)
        return {"dept": dept, "i": i, "status": getattr(e, "status_code", "ERR"),
                "headers": resp.headers if resp is not None else {}, "tokens": "",
                "ms": int((time.time() - t0) * 1000), "err": str(e)[:80]}


def run(dept: str, n: int, parallel: int, model: str) -> list[dict]:
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        return list(ex.map(lambda i: one(dept, i, model), range(n)))


def render(results: list[dict]) -> None:
    t = Table(title=f"Department '{results[0]['dept']}'")
    for c in ("#", "status", "tokens used", "remaining tokens", "retry-after", "ms", "apim-request-id"):
        t.add_column(c)
    for r in results:
        h = r["headers"]
        rem = common.header(h, "x-ratelimit-remaining-tokens", "x-remaining-tokens", "remaining-tokens")
        style = "red" if r["status"] == 429 else ("green" if r["status"] == 200 else "yellow")
        t.add_row(str(r["i"]), f"[{style}]{r['status']}[/{style}]", str(r["tokens"]), rem,
                  common.header(h, "retry-after"), str(r["ms"]), common.header(h, "apim-request-id"))
    console.print(t)
    n429 = sum(1 for r in results if r["status"] == 429)
    console.print(f"{r['dept']}: {n429}/{len(results)} throttled (429)\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--requests", type=int, default=12)
    p.add_argument("--parallel", type=int, default=6)
    p.add_argument("--model", default=None, help="deployment (default MODEL_SMALL)")
    p.add_argument("--no-compare", action="store_true", help="skip the 'ops' comparison run")
    a = p.parse_args()
    common.load_env(("APIM_GATEWAY_URL", "APIM_KEY_IT", "APIM_KEY_OPS"))
    model = a.model or common.env("MODEL_SMALL", "gpt-4.1-mini")
    it = run("it", a.requests, a.parallel, model)
    render(it)
    ops = []
    if not a.no_compare:
        ops = run("ops", a.requests, a.parallel, model)
        render(ops)
    got429 = any(r["status"] == 429 for r in it)
    console.print("[green]PASS[/green]: 'it' hit 429" if got429 else
                  "[red]FAIL[/red]: no 429 seen for 'it' (increase --requests / --parallel)")
    return 0 if got429 else 1


if __name__ == "__main__":
    sys.exit(main())
