import anyio
from mcp import types

from agent import mcp_server
from agent.tools import TOOL_SPECS


def test_lists_every_tool_with_its_schema():
    result = anyio.run(mcp_server.list_tools, None, None)
    assert [t.name for t in result.tools] == [s["name"] for s in TOOL_SPECS]
    assert result.tools[0].input_schema["type"] == "object"


def test_call_tool_runs_against_the_warehouse(mini_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_con", mini_db)
    params = types.CallToolRequestParams(name="coverage", arguments={"area": "Testby"})
    result = anyio.run(mcp_server.call_tool, None, params)
    assert not result.is_error and "pct_covered: 14.3" in result.content[0].text


def test_call_tool_errors_are_flagged(mini_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_con", mini_db)
    params = types.CallToolRequestParams(name="coverage", arguments={"area": "Atlantis"})
    result = anyio.run(mcp_server.call_tool, None, params)
    assert result.is_error and "Unknown area" in result.content[0].text


def test_server_sends_the_data_contract_as_instructions():
    server = mcp_server.build_server()
    assert "lower bound" in server.instructions
