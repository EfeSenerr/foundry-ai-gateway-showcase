"""Create one Foundry prompt agent per department (each in its own Foundry project) with the
department-scoped MCP endpoint as a tool (through the APIM MCP gateway with the team key when MCP_GATEWAY_URL is set).  Uses the azure-ai-projects v2 agents API.

  python create_foundry_agents.py            # create/update the 3 agents (model via APIM gateway connection)
  python create_foundry_agents.py --direct   # agents call the Foundry deployment directly (no APIM)
  python create_foundry_agents.py --test     # also ask each agent a question via the Responses API
  python create_foundry_agents.py --test-only
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console

import common

console = Console()

AGENT_NAMES = {"ops": "portops-ops-assistant", "it": "portops-it-assistant", "cs": "portops-cs-assistant"}
INSTRUCTIONS = {
    "ops": "You are the Terminal Ops Assistant for Northport Container Terminal (operations). "
           "Use the MCP tools for vessels, berths, cranes, containers and truck slots. Never invent data.",
    "it": "You are the IT Assistant for Northport Container Terminal. Use the MCP tools to search "
          "incident logs and summarise errors. Never invent data.",
    "cs": "You are the Customer Service Assistant for Northport Container Terminal. Use the MCP tools "
          "to track containers, check customs holds and book truck slots. Never invent data.",
}
TEST_QUESTIONS = {
    "ops": "Which vessels are scheduled to arrive, and at which berths?",
    "it": "Summarize the most recent errors from the incident logs.",
    "cs": "Which truck slots are available?",
}


def project_client(dept: str):
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    return AIProjectClient(endpoint=common.env(f"PROJECT_ENDPOINT_{dept.upper()}"),
                           credential=DefaultAzureCredential())


def agent_model(dept: str, via_gateway: bool) -> str:
    model = common.env("MODEL_SMALL", "gpt-4.1-mini")
    # Gateway connection created by scripts/setup-foundry-gateway.ps1 -> "<connection>/<deployment>"
    return f"apim-gateway-{dept}/{model}" if via_gateway else model


def create(dept: str, via_gateway: bool = True) -> None:
    from azure.ai.projects.models import MCPTool, PromptAgentDefinition
    project = project_client(dept)
    url = common.dept_mcp_url(dept)
    # Foundry does not persist `headers` in agent definitions: the team key travels in a CustomKeys project
    # connection (created by scripts/deploy-mcp.ps1) that Foundry sends as the api-key header.
    conn = f"mcp-gateway-{dept}" if common.mcp_headers_for(url) else None
    definition = PromptAgentDefinition(
        model=agent_model(dept, via_gateway),
        instructions=INSTRUCTIONS[dept],
        tools=[MCPTool(server_label="portops", server_url=url, require_approval="never",
                       allowed_tools=common.DEPARTMENT_TOOLS[dept], project_connection_id=conn)],
    )
    agent = project.agents.create_version(agent_name=AGENT_NAMES[dept], definition=definition)
    console.print(f"[green]created[/green] {dept}: {agent.name} v{agent.version} model={definition.model} -> {url}")


def invoke(dept: str, question: str) -> str:
    project = project_client(dept)
    openai_client = project.get_openai_client()
    resp = openai_client.responses.create(
        input=question,
        extra_body={"agent_reference": {"name": AGENT_NAMES[dept], "type": "agent_reference"}},
    )
    return resp.output_text


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--test", action="store_true", help="after creating, test each agent")
    p.add_argument("--test-only", action="store_true", help="only test existing agents")
    p.add_argument("--via-gateway", dest="via_gateway", action="store_true", default=True,
                   help="agents use the APIM gateway connection model apim-gateway-<dept>/<deployment> (default)")
    p.add_argument("--direct", dest="via_gateway", action="store_false",
                   help="agents call the Foundry deployment directly (bypasses APIM)")
    p.add_argument("--dept", choices=common.DEPARTMENTS, action="append", help="limit to department(s)")
    a = p.parse_args()
    depts = a.dept or list(common.DEPARTMENTS)
    common.load_env(("MCP_URL", *[f"PROJECT_ENDPOINT_{d.upper()}" for d in depts]))
    failed = 0
    for d in depts:
        try:
            if not a.test_only:
                create(d, a.via_gateway)
            if a.test or a.test_only:
                console.print(f"[bold]{d}[/bold] Q: {TEST_QUESTIONS[d]}")
                console.print(f"[bold]{d}[/bold] A: {invoke(d, TEST_QUESTIONS[d])}\n")
        except Exception as e:
            failed += 1
            console.print(f"[red]{d} failed:[/red] {type(e).__name__}: {str(e)[:400]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
