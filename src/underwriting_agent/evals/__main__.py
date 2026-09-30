"""Run the evaluation set and print a scorecard. See evals/__init__.py for usage."""

import argparse
import asyncio
import logging

import structlog

from underwriting_agent.config import Settings
from underwriting_agent.evals import (
    CaseResult,
    compare_providers,
    consistency,
    gate_failures,
    load_cases,
    run_eval,
    save_scorecard,
    summarize,
)
from underwriting_agent.llm import LLM, FallbackLLM, build_llm, build_provider

SHORT = {"auto_approve": "approve", "refer_to_underwriter": "refer", "auto_decline": "decline"}


def print_run(results: list[CaseResult], why: dict[str, str], llm_used: bool = False) -> None:
    print(
        f"{'case':<5}{'kind':<10}{'expected':<9}{'actual':<9}{'':<7}{'conf':<6}"
        f"{'secs':<7}{'flags':<22}why"
    )
    for r in results:
        mark = "ok" if r.correct else ("UNSAFE" if r.unsafe else "x")
        if llm_used and r.assessed_by == "rules" and not r.correct:
            mark = "n/a"  # the LLM did not answer; the rules decided
        conf = f"{r.confidence:.2f}" if r.confidence is not None else "-"
        secs = f"{r.latency_seconds:.1f}" if r.latency_seconds is not None else "-"
        print(
            f"{r.id:<5}{r.kind:<10}{SHORT.get(r.expected, r.expected):<9}"
            f"{SHORT.get(r.actual, r.actual):<9}{mark:<7}{conf:<6}{secs:<7}"
            f"{','.join(r.flags) or '-':<22}{why[r.id]}"
        )
        for rejected in r.rejected_flags:
            print(f"{'':<14}rejected LLM flag: {rejected}")


def print_comparison(by_provider: dict[str, list[CaseResult]]) -> None:
    summaries = {name: summarize(results) for name, results in by_provider.items()}
    keys = list(dict.fromkeys(k for s in summaries.values() for k in s))
    print(f"\n{'':<20}" + "".join(f"{name:<14}" for name in summaries))
    for key in keys:
        print(f"{key:<20}" + "".join(f"{str(s.get(key, '-')):<14}" for s in summaries.values()))


def _single_provider(settings: Settings, name: str) -> LLM:
    client = build_provider(settings, name)
    if client is None:
        raise SystemExit(f"No API key for {name!r} in .env.")
    return client


async def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m underwriting_agent.evals")
    parser.add_argument("--llm", action="store_true", help="add the LLM risk review")
    parser.add_argument("--provider", help="use only this provider (no fallback); implies --llm")
    parser.add_argument("--compare", help="comma-separated providers to compare, e.g. groq,gemini")
    parser.add_argument("--pace", type=float, default=0, help="seconds to wait between cases")
    parser.add_argument("--case", action="append", help="run only these case ids")
    parser.add_argument("--repeat", type=int, default=1, help="run the set N times")
    parser.add_argument("--save", action="store_true", help="write a scorecard to eval-results/")
    parser.add_argument("--gate", action="store_true", help="exit 1 on a regression (for CI)")
    args = parser.parse_args()

    # Keep the scorecard readable: only warnings from the pipeline.
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    settings = Settings()
    cases = [c for c in load_cases() if not args.case or c["id"] in args.case]
    why = {c["id"]: c["why"] for c in cases}

    if args.compare:
        names = [n.strip() for n in args.compare.split(",") if n.strip()]
        llms = {name: _single_provider(settings, name) for name in names}
        by_provider = await compare_providers(llms, cases, args.pace)
        for name, results in by_provider.items():
            print(f"\n=== {name} ({llms[name].model}) ===")
            print_run(results, why, llm_used=True)
        print_comparison(by_provider)
        if args.save:
            path = save_scorecard(
                list(by_provider.values()),
                {"mode": "compare", "models": [f"{n}/{llms[n].model}" for n in names]},
            )
            print(f"\nscorecard saved to {path}")
        return 0

    llm = None
    if args.provider:
        llm = _single_provider(settings, args.provider)
    elif args.llm:
        llm = build_llm(settings)
        if llm is None:
            print("No LLM keys found in .env; run without --llm or add a key.")
            return 1

    runs = []
    for i in range(args.repeat):
        if args.repeat > 1:
            print(f"\n=== run {i + 1} of {args.repeat} ===")
        results = await run_eval(llm, cases, args.pace)
        runs.append(results)
        print_run(results, why, llm_used=llm is not None)
        print()
        for key, value in summarize(results).items():
            print(f"{key:<20}{value}")

    if args.repeat > 1:
        stats = consistency(runs)
        print(
            f"\nconsistency         {stats['stable_cases']} cases gave the same decision every run"
        )
        for case_id, counts in stats["unstable"].items():
            print(f"  unstable {case_id}: {counts}")

    if args.save:
        if isinstance(llm, FallbackLLM):
            models = [f"{c.provider}/{c.model}" for c in llm.clients]
        else:
            models = [f"{llm.provider}/{llm.model}"] if llm else []
        path = save_scorecard(runs, {"mode": "llm" if llm else "rules", "models": models})
        print(f"\nscorecard saved to {path}")

    if args.gate:
        failures = [f for run in runs for f in gate_failures(run, llm is not None)]
        if failures:
            print("\nGATE FAILED:")
            for failure in failures:
                print(f"  {failure}")
            return 1
        print("\ngate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
