"""Run the evaluation set and print a scorecard. See evals/__init__.py for usage."""

import argparse
import asyncio
import logging

import structlog

from underwriting_agent.config import Settings
from underwriting_agent.evals import load_cases, run_eval, summarize
from underwriting_agent.llm import build_llm

SHORT = {"auto_approve": "approve", "refer_to_underwriter": "refer", "auto_decline": "decline"}


async def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m underwriting_agent.evals")
    parser.add_argument("--llm", action="store_true", help="add the LLM risk review")
    parser.add_argument("--case", action="append", help="run only these case ids")
    args = parser.parse_args()

    # Keep the scorecard readable: only warnings from the pipeline.
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

    llm = None
    if args.llm:
        llm = build_llm(Settings())
        if llm is None:
            print("No LLM keys found in .env; run without --llm or add a key.")
            return 1
    cases = [c for c in load_cases() if not args.case or c["id"] in args.case]
    results = await run_eval(llm, cases)
    why = {c["id"]: c["why"] for c in cases}

    print(f"{'case':<5}{'kind':<10}{'expected':<9}{'actual':<9}{'':<7}{'conf':<6}{'flags':<22}why")
    for r in results:
        mark = "ok" if r.correct else ("UNSAFE" if r.unsafe else "x")
        conf = f"{r.confidence:.2f}" if r.confidence is not None else "-"
        print(
            f"{r.id:<5}{r.kind:<10}{SHORT.get(r.expected, r.expected):<9}"
            f"{SHORT.get(r.actual, r.actual):<9}{mark:<7}{conf:<6}"
            f"{','.join(r.flags) or '-':<22}{why[r.id]}"
        )
        for rejected in r.rejected_flags:
            print(f"{'':<14}rejected LLM flag: {rejected}")

    print()
    for key, value in summarize(results).items():
        print(f"{key:<20}{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
