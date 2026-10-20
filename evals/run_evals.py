"""Run the eval suite: python -m evals.run_evals [--backend claude-code|api] [--only name] [--effort medium].

Each case is graded twice: an LLM judge compares the answer with a reference computed from the data,
and a faithfulness check flags numbers in the answer that no tool returned."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys

import pandas as pd

from agent.claude_code import ClaudeCodeAgent, child_env, find_claude
from agent.client import make_client
from agent.loop import MODEL, LockerAgent
from evals.cases import build_cases
from evals.faithfulness import unsupported_numbers
from pipeline.common import ROOT, connect

JUDGE_MODEL = "claude-opus-5-5"
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"passed": {"type": "boolean"}, "score": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["passed", "score", "reason"],
    "additionalProperties": False,
}
JUDGE_PROMPT = """You grade answers from a data agent about Swedish parcel lockers.

Question:
{question}

Reference answer (computed directly from the data):
{reference}

Grading criteria:
{criteria}

Agent answer:
{answer}

Judge only against the reference and the criteria. Extra correct context is fine; wrong numbers, missing required
items or missing required caveats are not. Score 1-5 (5 = fully correct and complete). passed = score >= 4."""


def judge_claude_code(question: str, reference: str, criteria: str, answer: str) -> dict:
    """The same judge run through headless Claude Code (Claude subscription, no API credits)."""
    prompt = JUDGE_PROMPT.format(question=question, reference=reference, criteria=criteria, answer=answer)
    cmd = [find_claude(), "-p", prompt, "--restricted", "--strict-mcp-config", "--tools", "",
           "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(VERDICT_SCHEMA),
           "--system-prompt", "You are a strict, fair grader. Return only the verdict.",
           "--model", "opus", "--effort", "medium"]
    out = subprocess.run(cmd, cwd=ROOT, env=child_env(), capture_output=True, text=True, encoding="utf-8",
                         errors="replace", stdin=subprocess.DEVNULL, timeout=600)
    try:
        data = json.loads(out.stdout)
        return data.get("structured_output") or json.loads(data["result"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return {"passed": False, "score": 0, "reason": f"judge failed: {(out.stderr or out.stdout)[:300]}"}


def judge(client, question: str, reference: str, criteria: str, answer: str) -> dict:
    response = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=4000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(
            question=question, reference=reference, criteria=criteria, answer=answer)}],
    )
    if response.stop_reason == "refusal":
        return {"passed": False, "score": 0, "reason": "judge declined"}
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run only cases whose name contains this text")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--backend", default="claude-code", choices=["claude-code", "api"])
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    con = connect(read_only=True)
    client = make_client() if args.backend == "api" else None
    cases = [c for c in build_cases(con) if not args.only or args.only in c.name]
    rows = []
    for case in cases:
        print(f"== {case.name}: {case.question}")
        if args.backend == "api":
            agent = LockerAgent(client, con, model=args.model or MODEL, effort=args.effort)
        else:
            agent = ClaudeCodeAgent(model=args.model or "opus", effort=args.effort)
        result = agent.ask(case.question)
        verdict = (judge(client, case.question, case.reference, case.criteria, result.answer) if client
                   else judge_claude_code(case.question, case.reference, case.criteria, result.answer))
        unsupported = unsupported_numbers(result.answer, [c["output"] for c in result.tool_calls], case.question)
        rows.append({"case": case.name, "backend": args.backend, "passed": verdict["passed"], "score": verdict["score"],
                     "unsupported_numbers": unsupported, "tool_calls": len(result.tool_calls),
                     "seconds": result.seconds, "input_tokens": result.usage.get("input_tokens", 0),
                     "output_tokens": result.usage.get("output_tokens", 0),
                     "tools_used": [c["name"] for c in result.tool_calls], "reason": verdict["reason"],
                     "answer": result.answer, "reference": case.reference})
        print(f"   {'PASS' if verdict['passed'] else 'FAIL'} ({verdict['score']}/5) · unsupported numbers: {unsupported or 'none'}")
        print(f"   {verdict['reason']}")
    out = ROOT / "runs" / f"evals_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    table = pd.DataFrame(rows)[["case", "passed", "score", "unsupported_numbers", "tool_calls", "seconds",
                                "input_tokens", "output_tokens"]]
    print("\n" + table.to_markdown(index=False))
    print(f"\npass rate {table.passed.mean():.0%} · results saved to {out}")
    return 0 if table.passed.all() else 1


if __name__ == "__main__":
    sys.exit(main())
