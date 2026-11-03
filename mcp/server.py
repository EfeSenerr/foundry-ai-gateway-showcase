"""Terminal Ops Assistant MCP server (FastMCP, streamable HTTP, stateless).

Endpoints:
  /mcp        all tools
  /ops/mcp    ops + container tools
  /it/mcp     IT log/incident tools
  /cs/mcp     container tools only
  /health     liveness probe
Set MCP_API_KEY to require header `x-mcp-key` on the MCP endpoints (/health stays open).
"""
from __future__ import annotations

import contextlib
import hmac
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from departments import ALL_TOOL_NAMES, DEPARTMENT_TOOLS
from tools import ALL_TOOLS

# Hostnames are not known up front (Container Apps FQDN), so host-header validation is off.
_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)

SCOPES = {"": ALL_TOOL_NAMES, **{dept: names for dept, names in DEPARTMENT_TOOLS.items()}}


def build_server(scope: str, names: list[str]) -> FastMCP:
    path = f"/{scope}/mcp" if scope else "/mcp"
    server = FastMCP(
        f"portops-{scope or 'all'}",
        instructions="Synthetic Northport Container Terminal data (terminals NCT-A, NCT-B). Demo only.",
        stateless_http=True,
        json_response=True,
        streamable_http_path=path,
        transport_security=_SECURITY,
    )
    for n in names:
        server.tool()(ALL_TOOLS[n])
    return server


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        key = os.environ.get("MCP_API_KEY")
        if key and request.url.path != "/health":
            if not hmac.compare_digest(request.headers.get("x-mcp-key", ""), key):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def create_app() -> Starlette:
    servers = [build_server(scope, names) for scope, names in SCOPES.items()]
    routes = [Route("/health", lambda r: JSONResponse({"status": "ok"}))]
    managers = []
    for s in servers:
        sub = s.streamable_http_app()
        routes.extend(sub.routes)
        managers.append(s.session_manager)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with contextlib.AsyncExitStack() as stack:
            for m in managers:
                await stack.enter_async_context(m.run())
            yield

    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(ApiKeyMiddleware)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
