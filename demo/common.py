"""Shared helpers for the PoC demo scripts (env loading, gateway client, header display)."""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
API_VERSION = "2024-10-21"
DEPARTMENTS = ("ops", "it", "cs")

# Tool sets per department (mirrors mcp/departments.py DEPARTMENT_TOOLS).
DEPARTMENT_TOOLS = {
    "ops": ["get_vessel_schedule", "get_berth_plan", "get_crane_assignments",
            "track_container", "get_yard_position", "check_customs_hold",
            "book_truck_slot", "list_truck_slots"],
    "it": ["search_incident_logs", "get_incident", "summarize_errors"],
    "cs": ["track_container", "get_yard_position", "check_customs_hold",
           "book_truck_slot", "list_truck_slots"],
}

# Headers worth showing when they come back from the gateway.
INTERESTING_HEADERS = (
    "apim-request-id", "x-portops-department", "x-portops-routed-model",
    "x-ratelimit-remaining-tokens", "x-tokens-consumed", "retry-after",
    "x-ms-region", "x-request-id", "x-ms-client-request-id",
)


def load_env(required: tuple[str, ...] = ()) -> None:
    """Load <repo>/.env and exit with a clear message if it or a variable is missing."""
    if not ENV_FILE.exists():
        sys.stderr.write(
            f"ERROR: {ENV_FILE} not found. Run scripts\\deploy.ps1 first (it writes .env), "
            "or copy .env.sample to .env and fill it in.\n")
        sys.exit(2)
    load_dotenv(ENV_FILE)
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.stderr.write(f"ERROR: missing variables in {ENV_FILE}: {', '.join(missing)}\n")
        sys.exit(2)


def env(name: str, default: str | None = None) -> str:
    return os.environ.get(name, default) or ""


def gateway_client(dept: str):
    """AzureOpenAI client that talks to the APIM AI Gateway with the department key."""
    from openai import AzureOpenAI
    return AzureOpenAI(
        azure_endpoint=env("APIM_GATEWAY_URL").rstrip("/"),
        api_key=env(f"APIM_KEY_{dept.upper()}"),
        api_version=API_VERSION,
        max_retries=0,
    )


def direct_mcp_url(dept: str) -> str:
    """Container App endpoint (MCP_URL ends with /mcp = all tools; scoped endpoint is /<dept>/mcp).
    Needs the backend key (x-mcp-key); normal consumers use the APIM gateway instead."""
    url = env("MCP_URL").rstrip("/")
    if url.endswith("/mcp"):
        url = url[: -len("/mcp")]
    return f"{url}/{dept}/mcp"


def gateway_mcp_url(dept: str) -> str:
    """Governed APIM MCP endpoint: MCP_GATEWAY_URL (= <gateway>/mcp) + /<dept>."""
    return f"{env('MCP_GATEWAY_URL').rstrip('/')}/{dept}"


def dept_mcp_url(dept: str) -> str:
    """MCP endpoint for a department: through the APIM gateway when MCP_GATEWAY_URL is set, else direct."""
    return gateway_mcp_url(dept) if env("MCP_GATEWAY_URL") else direct_mcp_url(dept)


def mcp_headers_for(url: str) -> dict[str, str]:
    """Auth headers for an MCP URL: team key (api-key) for gateway URLs; none otherwise."""
    gw = env("MCP_GATEWAY_URL").rstrip("/")
    if gw and url.startswith(gw + "/"):
        team = url[len(gw) + 1:].split("/")[0]
        key = env(f"MCP_KEY_{team.upper()}")
        return {"api-key": key} if key else {}
    return {}


@asynccontextmanager
async def mcp_client(url: str, headers: dict[str, str] | None = None):
    """Yield (read, write) streams of an MCP streamable-HTTP client with custom headers.
    Handles mcp 1.x (streamablehttp_client(headers=)) and 2.x (streamable_http_client(http_client=))."""
    headers = headers if headers is not None else mcp_headers_for(url)
    try:
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:
        streamablehttp_client = None
    if streamablehttp_client is not None:
        async with streamablehttp_client(url, headers=headers) as streams:
            yield streams[0], streams[1]
        return
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    async with create_mcp_http_client(headers=headers) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            yield streams[0], streams[1]


def pick_headers(headers) -> dict[str, str]:
    """Interesting gateway headers (plus any token-/ratelimit-related ones)."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in INTERESTING_HEADERS or "token" in lk or "ratelimit" in lk or lk.startswith("x-portops"):
            out[lk] = v
    return out


def header(headers, *names: str) -> str:
    for n in names:
        v = headers.get(n)
        if v is not None:
            return v
    return ""


# Illustrative list prices, USD per 1M tokens (input, output) - illustrative list price, verify.
PRICES = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = PRICES.get(model, PRICES["gpt-4.1-mini"])
    return prompt_tokens / 1e6 * pin + completion_tokens / 1e6 * pout
