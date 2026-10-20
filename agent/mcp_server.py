"""MCP server exposing the locker tools, so Claude Code or Claude Desktop can act as the agent
on a Claude subscription, without API credits.

Run by the client over stdio:  python -m agent.mcp_server
"""

from __future__ import annotations

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from agent.prompt import SYSTEM_PROMPT
from agent.tools import TOOL_SPECS, run_tool
from pipeline.common import DB_PATH, connect

NAME = "instabee-lockers"
_con = None


def _connection():
    global _con
    if _con is None:
        if not DB_PATH.exists():
            raise FileNotFoundError(f"{DB_PATH} not found; build it with: python -m pipeline.run_all")
        _con = connect(read_only=True)
    return _con


async def list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[
        types.Tool(name=s["name"], description=s["description"], input_schema=s["input_schema"]) for s in TOOL_SPECS])


async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    try:
        text, is_error = run_tool(_connection(), params.name, dict(params.arguments or {}))
    except Exception as exc:  # e.g. the database is missing
        text, is_error = f"ERROR: {type(exc).__name__}: {exc}", True
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)


def build_server() -> Server:
    return Server(NAME, version="0.1.0", title="Instabee locker network",
                  description="Coverage, gaps, site selection and demand tools over Swedish public locker data",
                  instructions=SYSTEM_PROMPT, on_list_tools=list_tools, on_call_tool=call_tool)


async def _main() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(_main)
