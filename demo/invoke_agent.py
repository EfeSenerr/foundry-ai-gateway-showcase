"""Invoke a department's Foundry prompt agent (created by create_foundry_agents.py).

  python invoke_agent.py --dept ops "Where is container HFCU1042428?"
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.panel import Panel

import common
from create_foundry_agents import AGENT_NAMES, invoke

console = Console()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dept", choices=common.DEPARTMENTS, required=True)
    p.add_argument("question")
    a = p.parse_args()
    common.load_env((f"PROJECT_ENDPOINT_{a.dept.upper()}",))
    try:
        answer = invoke(a.dept, a.question)
    except Exception as e:
        console.print(f"[red]Agent call failed:[/red] {type(e).__name__}: {str(e)[:500]}")
        return 1
    console.print(Panel(answer, title=f"{AGENT_NAMES[a.dept]} ({a.dept})", border_style="green"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
