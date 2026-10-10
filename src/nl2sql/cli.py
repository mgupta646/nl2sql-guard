"""`nl2sql ask "question"` · `nl2sql demo` · `nl2sql schema`"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import Agent
from .llm import get_llm
from .sample_data import connect

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "examples" / "data"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="nl2sql", description="Ask a DuckDB database questions in plain English, with SQL guardrails.")
    p.add_argument("--data", default=str(DATA), help="folder with stations.csv and trips.csv (generated if missing)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="answer one question with a live model")
    a.add_argument("question")
    a.add_argument("--provider", default="groq", choices=["groq", "openai", "anthropic"])
    a.add_argument("--model")
    a.add_argument("--max-rows", type=int, default=50)
    d = sub.add_parser("demo", help="rebuild the demo page from recorded replies")
    d.add_argument("--questions", default=str(ROOT / "examples" / "demo_questions.json"))
    d.add_argument("--out", default=str(ROOT / "docs" / "index.html"))
    sub.add_parser("schema", help="print the schema prompt the model sees")
    args = p.parse_args(argv)

    if args.cmd == "schema":
        print(Agent(connect(args.data), llm=None).schema)  # type: ignore[arg-type]
        return 0
    if args.cmd == "demo":
        from .demo import build
        answers = build(args.questions, args.data, args.out)
        for ans in answers:
            print(f"{'✓' if ans.ok else '✗'} {ans.question}  ({ans.message})")
        print(f"\ndemo page → {args.out}")
        return 0

    agent = Agent(connect(args.data), get_llm(args.provider, args.model), max_rows=args.max_rows)
    ans = agent.ask(args.question)
    for i, att in enumerate(ans.attempts, 1):
        print(f"\n— attempt {i} ({att.stage})\n{att.sql or att.raw.strip()}")
        if att.error:
            print(f"  ! {att.error}")
    print(f"\n{ans.message}")
    if ans.ok:
        print(" | ".join(ans.columns))
        for r in ans.rows[:20]:
            print(" | ".join(str(v) for v in r))
    return 0 if ans.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
