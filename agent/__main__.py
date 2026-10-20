"""Ask the locker agent a question: python -m agent "Where should the next 10 lockers go in Uppsala?" """

from __future__ import annotations

import argparse
import json
import sys

import anthropic

from agent.claude_code import ClaudeCodeAgent
from agent.client import make_client
from agent.loop import MODEL, LockerAgent
from pipeline.common import connect


def _trace(kind: str, payload: dict) -> None:
    if kind == "thinking":
        print(f"  · thinking: {payload['text'][:220].strip()}", file=sys.stderr)
    elif kind == "tool_call":
        print(f"  → {payload['name']}({json.dumps(payload['input'], ensure_ascii=False)})", file=sys.stderr)
    elif kind == "tool_result":
        status = "ERROR" if payload["is_error"] else "ok"
        first = payload["output"].splitlines()[0][:120] if payload["output"] else ""
        timing = f" in {payload['ms']} ms" if payload.get("ms") else ""
        print(f"  ← {status}{timing}: {first}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m agent", description=__doc__)
    parser.add_argument("question", nargs="*", help="the question; prompted for if omitted")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--backend", default="claude-code", choices=["claude-code", "api"],
                        help="claude-code: headless Claude Code on your Claude subscription (no API credits); "
                             "api: the Anthropic API loop (needs credits)")
    parser.add_argument("--model", default=None, help=f"default: opus (claude-code) or {MODEL} (api)")
    parser.add_argument("--quiet", action="store_true", help="hide the tool trace")
    args = parser.parse_args(argv)
    question = " ".join(args.question).strip() or input("Question: ").strip()
    if args.backend == "api":
        agent = LockerAgent(make_client(), connect(read_only=True), model=args.model or MODEL, effort=args.effort)
    else:
        agent = ClaudeCodeAgent(model=args.model or "opus", effort=args.effort)
    try:
        result = agent.ask(question, on_event=None if args.quiet else _trace)
    except anthropic.AuthenticationError:
        print("Authentication failed: check ANTHROPIC_API_KEY in .env.", file=sys.stderr)
        return 2
    except anthropic.BadRequestError as exc:
        text = str(exc).lower()
        hint = (" Add ANTHROPIC_WORKSPACE_ID=<id> to .env or use a workspace-scoped key." if "workspace" in text else
                " Add credits under Plans & Billing in the Anthropic Console." if "credit balance" in text else "")
        print(f"Request rejected: {exc.message}.{hint}", file=sys.stderr)
        return 2
    print("\n" + result.answer)
    u = result.usage
    print(f"\n[{len(result.tool_calls)} tool calls · {result.seconds}s · in {u['input_tokens']:,} "
          f"(cache read {u['cache_read_input_tokens']:,}) · out {u['output_tokens']:,} tokens · {result.stop_reason}]",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
