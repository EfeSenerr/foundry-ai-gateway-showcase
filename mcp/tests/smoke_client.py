import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

CONTAINER_ID = json.loads((Path(__file__).parent.parent / "data" / "containers.json").read_text(encoding="utf-8"))[0]["container_id"]
CALLS = {
    "/mcp": ("get_vessel_schedule", {"status": "berthed"}),
    "/ops/mcp": ("get_berth_plan", {"berth": "A1"}),
    "/it/mcp": ("summarize_errors", {"hours": 24}),
    "/cs/mcp": ("track_container", {"container_id": CONTAINER_ID}),
}
EXPECTED = {
    "/mcp": 11, "/ops/mcp": 8, "/it/mcp": 3, "/cs/mcp": 5,
}


async def check(base: str, path: str, headers: dict) -> None:
    async with streamablehttp_client(base + path, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            assert len(tools) == EXPECTED[path], f"{path}: expected {EXPECTED[path]} tools, got {tools}"
            name, args = CALLS[path]
            res = await s.call_tool(name, args)
            assert not res.isError, f"{path}: {name} failed: {res.content}"
            text = res.content[0].text
            json.loads(text)
            print(f"OK {path}: {len(tools)} tools {tools}; {name} -> {text[:90]}...")


async def main(base: str, key: str | None) -> None:
    headers = {"x-mcp-key": key} if key else {}
    for path in CALLS:
        await check(base.rstrip("/"), path, headers)
    print("SMOKE PASSED")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python smoke_client.py <base-url> [api-key]   e.g. http://localhost:8000")
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
