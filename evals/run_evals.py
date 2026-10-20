"""Run the eval suite: python -m evals.run_evals [--only name] [--effort medium].

Each case is graded twice: an LLM judge compares the answer with a reference computed from the data,
and a faithfulness check flags numbers in the answer that no tool returned."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pandas as pd

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
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    con, client = connect(read_only=True), make_client()
    cases = [c for c in build_cases(con) if not args.only or args.only in c.name]
    rows = []
    for case in cases:
        print(f"== {case.name}: {case.question}")
        agent = LockerAgent(client, con, model=args.model, effort=args.effort)
        result = agent.ask(case.question)
        verdict = judge(client, case.question, case.reference, case.criteria, result.answer)
        unsupported = unsupported_numbers(result.answer, [c["output"] for c in result.tool_calls], case.question)
        rows.append({"case": case.name, "passed": verdict["passed"], "score": verdict["score"],
                     "unsupported_numbers": unsupported, "tool_calls": len(result.tool_calls),
                     "seconds": result.seconds, "input_tokens": result.usage["input_tokens"],
                     "output_tokens": result.usage["output_tokens"], "reason": verdict["reason"],
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
