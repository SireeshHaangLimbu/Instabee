"""End-to-end check: start the MCP server over stdio, as Claude Code does, and call two tools.

Run: .venv/Scripts/python scripts/smoke_mcp.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "agent.mcp_server"], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.server_info.name, "| instructions:", len(init.instructions or ""), "chars")
            tools = await session.list_tools()
            print("tools:", ", ".join(t.name for t in tools.tools))
            for name, args in [("coverage", {"area": "Uppsala"}), ("optimise_next_n", {"area": "Uppsala", "n": 3})]:
                result = await session.call_tool(name, args)
                print(f"\n{name}({args}) error={result.is_error}\n{result.content[0].text[:700]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    anyio.run(main)
