"""Offline end-to-end check: no server, no API keys, no network.
Exercises every orchestration branch through the mock provider:
routing (sql / clarify / chit_chat), guardrail block, human-in-the-loop
review (approve / reject / modify), and the repair loop."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LLM_PROVIDER", "mock")

from app.agent import SQLAgent            # noqa: E402
from app import guardrails                # noqa: E402
from app.config import settings           # noqa: E402
from app.trace import Trace               # noqa: E402


def check(label, ok):
    print(f"[{'OK' if ok else 'FAIL'}] {label}")
    return ok


def main():
    agent = SQLAgent()
    passed = True

    # Routing
    r = agent.run("How many incidents by severity?", "smoke")
    passed &= check("sql route executes", r["route"] == "sql" and r["status"] == "completed" and r["row_count"] > 0)
    passed &= check("trace has plan+generate+guardrail+execute",
                    [s["name"] for s in r["trace"]] == ["plan", "generate_sql", "guardrail", "execute"])

    r = agent.run("hello there", "smoke")
    passed &= check("chit_chat route skips SQL", r["route"] == "chit_chat" and not r["sql"])

    r = agent.run("incidents", "smoke")
    passed &= check("clarify route asks a question", r["route"] == "clarify" and bool(r["message"]))

    # Guardrail blocks
    passed &= check("DROP blocked", guardrails.static_check("DROP TABLE x", settings.max_joins).decision == "block")
    passed &= check("DELETE blocked", guardrails.static_check("DELETE FROM x", settings.max_joins).decision == "block")
    passed &= check("multi-statement blocked",
                    guardrails.static_check("SELECT 1; DROP TABLE x", settings.max_joins).decision == "block")

    # Human-in-the-loop (force review with a tiny row threshold)
    settings.max_result_rows = 10
    r = agent.run("List all open or unresolved incidents", "smoke")
    passed &= check("large result flagged for review", r["needs_approval"] and r["status"] == "needs_approval")
    tid = r["turn_id"]
    ra = agent.review(tid, "approve")
    passed &= check("approve executes flagged query", ra["status"] == "completed" and ra["row_count"] > 0)

    r = agent.run("List all open or unresolved incidents", "smoke")
    rr = agent.review(r["turn_id"], "reject", reason="too broad")
    passed &= check("reject stops execution", rr["status"] == "rejected")

    r = agent.run("List all open or unresolved incidents", "smoke")
    rm = agent.review(r["turn_id"], "modify", modified_sql="SELECT COUNT(*) AS n FROM safety_incidents")
    passed &= check("modify runs the edited SQL", rm["status"] == "completed" and rm["rows"] == [[756]])
    settings.max_result_rows = 200

    # Repair loop
    tr = Trace()
    _, _, _, rows, err = agent._execute_with_repair(
        "how many incidents", "SELECT nope FROM safety_incidents", "bad", tr)
    passed &= check("repair recovers from a bad query",
                    err is None and [s["name"] for s in tr.to_list()] == ["execute", "repair", "execute"])

    print("\nsmoke test:", "PASSED" if passed else "FAILED")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
