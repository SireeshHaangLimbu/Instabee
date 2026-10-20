import json

from agent import claude_code

STREAM = [
    {"type": "system", "subtype": "init", "apiKeySource": "none", "model": "claude-opus-5-5"},
    {"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": "Size it for December."},
        {"type": "tool_use", "id": "t1", "name": "mcp__instabee-lockers__peak_sizing", "input": {"annual_locker_parcels": 20000}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "- compartments_peak: 171"}]}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "About 171 compartments."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "About 171 compartments.", "num_turns": 2,
     "duration_ms": 4139, "total_cost_usd": 0.03, "stop_reason": "end_turn",
     "usage": {"input_tokens": 4, "output_tokens": 160, "cache_read_input_tokens": 13093, "cache_creation_input_tokens": 2737}},
]


def test_parse_stream_collects_answer_tool_calls_and_usage():
    lines = [json.dumps(e) for e in STREAM] + ["not json"]
    result = claude_code.parse_stream(lines, question="peak?")
    assert result.answer == "About 171 compartments."
    assert result.tool_calls == [{"name": "peak_sizing", "input": {"annual_locker_parcels": 20000},
                                  "output": "- compartments_peak: 171", "is_error": False, "ms": None}]
    assert result.thinking == ["Size it for December."]
    assert result.usage["output_tokens"] == 160 and result.stop_reason == "end_turn"
    assert result.model == "claude-opus-5-5" and result.extra["auth"] == "none"


def test_command_restricts_claude_to_the_locker_tools(tmp_path):
    cmd = claude_code.build_command("claude.exe", "Where?", mcp_config=tmp_path / ".mcp.json", model="opus", effort="medium")
    assert cmd[:3] == ["claude.exe", "-p", "Where?"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__instabee-lockers"
    assert "--strict-mcp-config" in cmd and "--restricted" in cmd
    assert "lower bound" in cmd[cmd.index("--system-prompt") + 1]


def test_child_environment_drops_api_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "w")
    env = claude_code.child_env()
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_WORKSPACE_ID" not in env
