"""Run the agent through headless Claude Code with the locker MCP server.

Claude Code signed in with a Claude subscription is the model; the MCP server supplies the same tools as the
API agent. No API credits are used: the API keys are removed from the child process environment.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

from agent.loop import LOG_PATH, AgentResult, log_result
from agent.prompt import SYSTEM_PROMPT
from pipeline.common import ROOT

SERVER = "instabee-lockers"
PREFIX = f"mcp__{SERVER}__"
MCP_CONFIG = ROOT / ".mcp.json"


def find_claude() -> str:
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit:
        return explicit
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    bundled = sorted(glob.glob(str(Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude*")))
    if bundled:
        return bundled[-1]
    raise FileNotFoundError("Claude Code not found: install it or set CLAUDE_BIN to the claude executable.")


def mcp_config() -> dict:
    python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return {"mcpServers": {SERVER: {
        "type": "stdio",
        "command": python.as_posix(),
        "args": ["-m", "agent.mcp_server"],
        "env": {"PYTHONPATH": ROOT.as_posix(), "PYTHONIOENCODING": "utf-8"},
    }}}


def ensure_mcp_config(overwrite: bool = False) -> Path:
    if overwrite or not MCP_CONFIG.exists():
        MCP_CONFIG.write_text(json.dumps(mcp_config(), indent=2), encoding="utf-8")
    return MCP_CONFIG


def build_command(claude: str, question: str, mcp_config: Path, model: str = "opus", effort: str = "medium") -> list[str]:
    return [
        claude, "-p", question,
        "--system-prompt", SYSTEM_PROMPT,
        "--mcp-config", str(mcp_config), "--strict-mcp-config",
        "--restricted",                       # ignore personal settings, hooks and plugins
        "--tools", "",                        # no built-in tools: only the locker MCP tools
        "--allowedTools", f"mcp__{SERVER}",   # run them without permission prompts
        "--no-session-persistence",
        "--output-format", "stream-json", "--verbose",
        "--model", model, "--effort", effort,
    ]


def child_env() -> dict:
    env = dict(os.environ)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_WORKSPACE_ID"):
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _tool_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def parse_stream(lines: Iterable[str], question: str = "",
                 on_event: Callable[[str, dict], None] | None = None) -> AgentResult:
    emit = on_event or (lambda kind, payload: None)
    result = AgentResult(question=question, answer="", stop_reason="", model="", usage={})
    pending: dict[str, dict] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            result.model = event.get("model", "")
            result.extra["auth"] = event.get("apiKeySource")
        elif kind == "assistant":
            for block in event["message"]["content"]:
                if block.get("type") == "thinking" and block.get("thinking"):
                    result.thinking.append(block["thinking"])
                    emit("thinking", {"text": block["thinking"]})
                elif block.get("type") == "tool_use":
                    call = {"name": block["name"].removeprefix(PREFIX), "input": block.get("input", {}),
                            "output": "", "is_error": False, "ms": None}
                    pending[block["id"]] = call
                    result.tool_calls.append(call)
                    emit("tool_call", call)
        elif kind == "user":
            for block in event["message"]["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                    call = pending[block["tool_use_id"]]
                    call["output"] = _tool_text(block.get("content"))
                    call["is_error"] = bool(block.get("is_error"))
                    emit("tool_result", call)
        elif kind == "result":
            result.answer = (event.get("result") or "").strip()
            result.stop_reason = event.get("stop_reason") or event.get("subtype", "")
            usage = event.get("usage") or {}
            result.usage = {k: usage.get(k, 0) for k in
                            ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
            result.seconds = round((event.get("duration_ms") or 0) / 1000, 1)
            result.extra.update({"num_turns": event.get("num_turns"), "api_equivalent_cost_usd": event.get("total_cost_usd"),
                                 "is_error": event.get("is_error")})
    return result


class ClaudeCodeAgent:
    """Same interface as LockerAgent.ask, backed by headless Claude Code."""

    def __init__(self, model: str = "opus", effort: str = "medium", timeout_s: int = 600, log: bool = True):
        self.model, self.effort, self.timeout_s, self.log = model, effort, timeout_s, log

    def ask(self, question: str, on_event: Callable[[str, dict], None] | None = None) -> AgentResult:
        cmd = build_command(find_claude(), question, ensure_mcp_config(), model=self.model, effort=self.effort)
        started = time.time()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=child_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        lines = []

        def _lines():
            for line in proc.stdout:
                lines.append(line)
                yield line

        result = parse_stream(_lines(), question=question, on_event=on_event)
        try:
            proc.wait(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            result.stop_reason = "timeout"
        if not result.answer:
            err = (proc.stderr.read() if proc.stderr else "").strip()
            tail = "".join(lines[-3:]).strip()
            result.answer = f"Claude Code returned no answer (exit {proc.returncode}). {err or tail}"[:2000]
            result.stop_reason = result.stop_reason or "error"
        if not result.seconds:
            result.seconds = round(time.time() - started, 1)
        if on_event:
            on_event("answer", {"text": result.answer})
        if self.log:
            log_result(result, LOG_PATH)
        return result
