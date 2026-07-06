"""Run the guardrail + routing regression suite and print a scored report.
Exits non-zero if any case fails, so it works as a CI gate.

Uses the mock provider (deterministic) so results are stable and need no key."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LLM_PROVIDER", "mock")

from app import evals            # noqa: E402
from app.agent import SQLAgent   # noqa: E402


def main():
    report = evals.run(agent=SQLAgent())

    print("== guardrail cases ==")
    for r in report["guardrail_cases"]:
        mark = "ok " if r["pass"] else "XX "
        print(f"[{mark}] {r['expected']:28} got {r['got']:28} {r['sql'][:50]}")

    print("\n== flow cases ==")
    for r in report["flow_cases"]:
        mark = "ok " if r["pass"] else "XX "
        print(f"[{mark}] {r['expected']:12} got {r['got']:12} {r['question'][:50]}")

    print(f"\nscore: {report['passed']}/{report['total']} "
          f"({report['score'] * 100:.0f}%)  failed: {report['failed']}")
    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
