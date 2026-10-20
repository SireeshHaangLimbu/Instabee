"""The agent loop: Claude proposes tool calls, Python runs them, until Claude writes the answer."""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from agent.prompt import SYSTEM_PROMPT
from agent.tools import TOOL_SPECS, run_tool
from pipeline.common import ROOT

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
LOG_PATH = ROOT / "runs" / "agent_runs.jsonl"


@dataclass
class AgentResult:
    question: str
    answer: str
    stop_reason: str
    tool_calls: list[dict] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0
    model: str = MODEL


def _text(content) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


class LockerAgent:
    def __init__(self, client, con, model: str = MODEL, effort: str = "medium", max_turns: int = 25,
                 max_tokens: int = 16000, log_path: Path | None = LOG_PATH):
        self.client, self.con, self.model, self.effort = client, con, model, effort
        self.max_turns, self.max_tokens, self.log_path = max_turns, max_tokens, log_path

    def _create(self, messages: list):
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            tools=TOOL_SPECS,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def ask(self, question: str, on_event: Callable[[str, dict], None] | None = None) -> AgentResult:
        emit = on_event or (lambda kind, payload: None)
        started = time.time()
        messages: list = [{"role": "user", "content": question}]
        result = AgentResult(question=question, answer="", stop_reason="", model=self.model,
                             usage={"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                                    "cache_creation_input_tokens": 0})
        for _ in range(self.max_turns):
            response = self._create(messages)
            for key in result.usage:
                result.usage[key] += getattr(response.usage, key, 0) or 0
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None)
                result.stop_reason = "refusal"
                result.answer = f"The model declined this request (category: {category}). Try rephrasing the question."
                break
            for block in response.content:
                if getattr(block, "type", None) == "thinking" and getattr(block, "thinking", ""):
                    result.thinking.append(block.thinking)
                    emit("thinking", {"text": block.thinking})
            messages.append({"role": "assistant", "content": response.content})  # unchanged, thinking included
            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                result.stop_reason = response.stop_reason
                result.answer = _text(response.content)
                break
            tool_results = []
            for block in tool_uses:
                emit("tool_call", {"name": block.name, "input": block.input})
                t0 = time.time()
                output, is_error = run_tool(self.con, block.name, block.input)
                call = {"name": block.name, "input": block.input, "output": output, "is_error": is_error,
                        "ms": round(1000 * (time.time() - t0))}
                result.tool_calls.append(call)
                emit("tool_result", call)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": output,
                                     "is_error": is_error})
            messages.append({"role": "user", "content": tool_results})  # all results in one message
        else:
            result.stop_reason = "max_turns"
            result.answer = f"Stopped after {self.max_turns} model turns without a final answer."
        result.seconds = round(time.time() - started, 1)
        emit("answer", {"text": result.answer})
        self._log(result)
        return result

    def _log(self, result: AgentResult) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **asdict(result)}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
