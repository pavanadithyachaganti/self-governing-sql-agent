"""Offline end-to-end check: no server, no API keys, no network.
Runs the mock provider through the full question -> SQL -> execute loop."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent import SQLAgent  # noqa: E402

QUESTIONS = [
    "How many incidents are there by severity?",
    "Show me all critical incidents.",
    "What is the average heart rate and body temp per worker?",
    "Which sites have the highest productivity?",
    "List all open or unresolved incidents.",
]


def main():
    agent = SQLAgent()
    ok = True
    for q in QUESTIONS:
        result = agent.run(q, session_id="smoke_test")
        status = "OK" if not result["error"] else "ERROR"
        if result["error"]:
            ok = False
        print(f"[{status}] Q: {q}")
        print(f"   SQL: {result['sql']}")
        print(f"   rows: {result['row_count']}  ({result['total_ms']} ms, provider={result['provider']})")
        if result["error"]:
            print(f"   error: {result['error']}")
    print("\nsmoke test:", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
