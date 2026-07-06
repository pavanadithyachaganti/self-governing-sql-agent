"""Regression harness. Turns "the guardrails and routing work" into a measured,
repeatable number. Two suites:

  guardrail_cases - feed SQL straight through the guardrail rules and assert the
                    decision (and, for blocks, the specific rule).
  flow_cases      - run whole questions through the agent (mock provider, so the
                    outcome is deterministic) and assert the orchestration route.

Run with scripts/run_eval.py or via POST /api/eval.
"""
import os
import json
from .config import settings
from . import guardrails
from .db import estimate_rows

EVAL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "eval_set.json")


def _load():
    with open(EVAL_PATH) as f:
        return json.load(f)


def _guardrail_outcome(sql):
    """Reproduce the agent's guardrail decision for a raw SQL string."""
    static = guardrails.static_check(sql, settings.max_joins)
    if static.decision == "block":
        decision = static
    else:
        sens = guardrails.sensitivity_check(sql)
        size = guardrails.size_check(estimate_rows(sql), settings.max_result_rows, sql)
        decision = guardrails.combine(static, sens, size)
    return f"{decision.decision}:{decision.rule}" if decision.decision == "block" else decision.decision


def _flow_outcome(result):
    """Collapse a full agent result into a single outcome label."""
    if result["route"] in ("chit_chat", "clarify", "restricted"):
        return result["route"]
    if result["needs_approval"]:
        return "review"
    if result["status"] == "blocked":
        return f"block:{result.get('guardrail_rule') or ''}"
    if result["status"] == "completed":
        return "allow"
    return result["status"]


def run(agent=None):
    data = _load()
    guardrail_results, flow_results = [], []

    for c in data.get("guardrail_cases", []):
        got = _guardrail_outcome(c["sql"])
        guardrail_results.append({
            "sql": c["sql"], "note": c.get("note", ""),
            "expected": c["expect"], "got": got, "pass": got == c["expect"],
        })

    if agent is not None:
        for c in data.get("flow_cases", []):
            got = _flow_outcome(agent.run(c["question"], session_id="_eval"))
            flow_results.append({
                "question": c["question"],
                "expected": c["expect"], "got": got, "pass": got == c["expect"],
            })

    all_results = guardrail_results + flow_results
    passed = sum(1 for r in all_results if r["pass"])
    return {
        "total": len(all_results),
        "passed": passed,
        "failed": len(all_results) - passed,
        "score": round(passed / len(all_results), 3) if all_results else 1.0,
        "guardrail_cases": guardrail_results,
        "flow_cases": flow_results,
    }
