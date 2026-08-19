"""Step 0: enumerate the tools/schemas the local cTrader MCP server exposes.

Run this first, before trusting src/ctrader_bot/mcp_client.py — that module's
function names and argument shapes were written against assumptions about a
typical cTrader Open API surface and must be reconciled against whatever this
script actually prints.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ctrader_bot.config import load_secrets


async def main() -> None:
    secrets = load_secrets()
    url = secrets.ctrader_mcp_url
    print(f"Connecting to {url} ...")

    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"\n{len(tools.tools)} tools available:\n")
            for tool in tools.tools:
                print(f"- {tool.name}")
                if tool.description:
                    print(f"    {tool.description}")
                print(f"    input schema: {json.dumps(tool.inputSchema if hasattr(tool, 'inputSchema') else tool.input_schema)}")
                print()

            try:
                resources = await session.list_resources()
                if resources.resources:
                    print(f"\n{len(resources.resources)} resources available:\n")
                    for r in resources.resources:
                        print(f"- {r.uri}  ({r.name})")
            except Exception as e:  # noqa: BLE001
                print(f"(list_resources not supported or failed: {e})")


if __name__ == "__main__":
    asyncio.run(main())
