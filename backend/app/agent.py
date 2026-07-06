"""Multi-step orchestrator.

A question is coordinated through named steps, each recorded in the trace:

    plan ─┬─ chit_chat / clarify ──────────────► answer (no SQL)
          └─ sql ─► generate ─► guardrail ─┬─ block  ─► refuse (no SQL run)
                                           ├─ review ─► pause for a human
                                           └─ allow  ─► execute ─► repair? ─► answer

The guardrail 'review' branch is the human-in-the-loop checkpoint: the turn is
saved as 'needs_approval' and returned to the caller without touching the
database. A later /api/review call resumes it (approve / reject / modify).
"""
import json
from .db import SCHEMA_DESCRIPTION, estimate_rows, run_query
from .llm import get_llm, complete_json
from . import memory, guardrails
from .config import settings
from .trace import Trace, span

PLAN_SYSTEM = f"""STEP=plan
You are the router for a natural-language interface to a safety operations database.
Classify the user's question into exactly one route:
- "sql": it asks for data that requires querying the database.
- "restricted": it asks to view an individual worker's restricted personal data — national ID,
  home address, phone number, medical conditions, or salary. Refuse these at the door.
  NOTE: aggregate statistics over those fields (e.g. "average salary by site", "how many workers
  have each medical condition") are NOT restricted — route those to "sql".
- "clarify": it is too vague or ambiguous to answer; you need one clarifying question.
- "chit_chat": it is a greeting, thanks, or a question about what you can do (no data needed).

Database subject matter (for judging relevance):
{SCHEMA_DESCRIPTION}

Respond with strict JSON only, no markdown:
{{"route": "sql|restricted|clarify|chit_chat", "message": "<clarifying question, short reply, or refusal; empty for sql>"}}
"""

GENERATE_SYSTEM = f"""STEP=generate
You are a SQL analyst for an industrial safety operations database.
Translate the user's question into a single read-only SQLite SELECT query.

Schema:
{SCHEMA_DESCRIPTION}

Rules:
- Only ever write a single SELECT query. Never write, alter, or delete data.
- Use only the tables and columns listed above.
- Add a LIMIT when the question implies a small "top N"; otherwise return the natural result.
- Respond with strict JSON only, no markdown: {{"sql": "<query>", "explanation": "<one sentence>"}}
"""

REPAIR_SYSTEM = f"""STEP=repair
A SQL query you wrote failed to execute. Fix it.

Schema:
{SCHEMA_DESCRIPTION}

You will be given the original question, the failed SQL, and the database error.
Return a corrected single read-only SELECT query.
Respond with strict JSON only, no markdown: {{"sql": "<query>", "explanation": "<one sentence>"}}
"""

SUMMARIZE_SYSTEM = """STEP=summarize
You are given a user's question and the exact rows a SQL query returned.
Write a concise, direct answer (1-3 sentences) using ONLY numbers and facts present
in those rows. Do not invent values, do not extrapolate beyond the rows shown.
Respond with strict JSON only, no markdown: {"answer": "<your answer>"}
"""

FAITHFULNESS_SYSTEM = """STEP=faithfulness
You are a strict grader. You are given a proposed answer and the exact data rows it
should be based on. Score how fully the answer is supported by the rows, from 0.0
(fabricated / unsupported) to 1.0 (every claim is directly supported).
Respond with strict JSON only, no markdown: {"score": <0.0-1.0>, "reason": "<one sentence>"}
"""


class SQLAgent:
    def __init__(self):
        self.llm = get_llm()

    # ---- individual steps -------------------------------------------------

    def _plan(self, question, trace):
        with span(trace, "plan") as m:
            parsed = complete_json(self.llm, PLAN_SYSTEM, question)
            route = parsed.get("route", "sql")
            if route not in ("sql", "restricted", "clarify", "chit_chat"):
                route = "sql"
            message = parsed.get("message", "")
            m["detail"] = f"route = {route}"
            m["meta"] = {"route": route}
            return route, message

    def _generate(self, question, session_id, trace):
        with span(trace, "generate_sql") as m:
            history = memory.context_for_prompt(session_id)
            user = (f"Recent conversation:\n{history}\n\nNew question: {question}"
                    if history else question)
            parsed = complete_json(self.llm, GENERATE_SYSTEM, user)
            sql = (parsed.get("sql") or "").strip()
            explanation = parsed.get("explanation", "")
            m["detail"] = sql or "(no query produced)"
            return sql, explanation

    def _guardrail(self, sql, trace):
        with span(trace, "guardrail") as m:
            static = guardrails.static_check(sql, settings.max_joins)
            decision = static
            meta = {}
            if static.decision != "block":
                sensitivity = guardrails.sensitivity_check(sql)
                est = estimate_rows(sql)
                size = guardrails.size_check(est, settings.max_result_rows, sql)
                decision = guardrails.combine(static, sensitivity, size)
                meta = {"row_estimate": est}
            m["detail"] = f"{decision.decision}: {decision.rule}"
            m["meta"] = {**meta, "decision": decision.decision,
                         "rule": decision.rule, "reason": decision.reason}
            return decision

    def _repair(self, question, bad_sql, error, trace):
        with span(trace, "repair") as m:
            user = (f"Question: {question}\nFailed SQL: {bad_sql}\nError: {error}\n"
                    "Return corrected SQL.")
            parsed = complete_json(self.llm, REPAIR_SYSTEM, user)
            sql = (parsed.get("sql") or "").strip()
            explanation = parsed.get("explanation", "")
            m["detail"] = sql or "(no repair produced)"
            return sql, explanation

    def _execute_with_repair(self, question, sql, explanation, trace):
        """Run the query; on a SQL error, ask the model to fix it and retry,
        up to settings.max_repairs times. Returns the final state."""
        attempt_sql, attempt_expl = sql, explanation
        for attempt in range(settings.max_repairs + 1):
            with span(trace, "execute") as m:
                columns, rows, error = run_query(attempt_sql)
                m["detail"] = f"{len(rows)} rows" if not error else f"error: {error}"
            if not error:
                return attempt_sql, attempt_expl, columns, rows, None
            if attempt >= settings.max_repairs:
                return attempt_sql, attempt_expl, [], [], error
            # Try to repair, but re-check the fix through the guardrail first.
            new_sql, new_expl = self._repair(question, attempt_sql, error, trace)
            if not new_sql:
                return attempt_sql, attempt_expl, [], [], error
            recheck = guardrails.static_check(new_sql, settings.max_joins)
            if recheck.decision == "block":
                return new_sql, new_expl, [], [], f"repair blocked by guardrail: {recheck.reason}"
            attempt_sql, attempt_expl = new_sql, new_expl or attempt_expl
        return attempt_sql, attempt_expl, [], [], error

    def _rows_as_text(self, columns, rows):
        capped = rows[:settings.summarize_max_rows]
        lines = [" | ".join(columns)]
        for r in capped:
            lines.append(" | ".join("" if v is None else str(v) for v in r))
        if len(rows) > len(capped):
            lines.append(f"... ({len(rows) - len(capped)} more rows not shown)")
        return "\n".join(lines)

    def _summarize(self, question, columns, rows, trace):
        """Turn result rows into a grounded natural-language answer, then verify
        the answer is actually supported by those rows (faithfulness)."""
        table = self._rows_as_text(columns, rows)
        with span(trace, "summarize") as m:
            parsed = complete_json(self.llm, SUMMARIZE_SYSTEM,
                                   f"Question: {question}\n\nRows:\n{table}")
            answer = (parsed.get("answer") or "").strip()
            m["detail"] = answer or "(no summary)"
        score, reason = None, ""
        if answer:
            with span(trace, "verify") as m:
                graded = complete_json(self.llm, FAITHFULNESS_SYSTEM,
                                       f"Answer: {answer}\n\nRows:\n{table}")
                try:
                    score = round(float(graded.get("score")), 2)
                except (TypeError, ValueError):
                    score = None
                reason = graded.get("reason", "")
                m["detail"] = f"faithfulness = {score}" if score is not None else "faithfulness = n/a"
                m["meta"] = {"score": score, "reason": reason, "grounded":
                             (score is None or score >= settings.faithfulness_threshold)}
        return answer, score

    # ---- assembling a response -------------------------------------------

    def _response(self, question, trace, route="sql", sql="", explanation="",
                  columns=None, rows=None, error=None, guardrail=None,
                  status="completed", needs_approval=False, message="", turn_id=None,
                  answer="", faithfulness=None):
        columns = columns or []
        rows = rows or []
        return {
            "question": question,
            "route": route,
            "status": status,
            "needs_approval": needs_approval,
            "message": message,
            "sql": sql,
            "explanation": explanation,
            "answer": answer,
            "faithfulness": faithfulness,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "error": error,
            "guardrail_decision": (guardrail.decision if guardrail else None),
            "guardrail_rule": (guardrail.rule if guardrail else None),
            "guardrail_reason": (guardrail.reason if guardrail else None),
            "provider": self.llm.name,
            "trace": trace.to_list(),
            "total_ms": trace.total_ms,
            "turn_id": turn_id,
        }

    # ---- entry points -----------------------------------------------------

    def run(self, question, session_id="default"):
        trace = Trace()
        route, message = self._plan(question, trace)

        # Branch A: no database access needed.
        if route in ("clarify", "chit_chat"):
            resp = self._response(question, trace, route=route, status=route, message=message)
            resp["turn_id"] = memory.create_turn(
                session_id, question, route=route, status=route,
                final_answer=message, trace=trace.to_list())
            return resp

        # Branch A': early confidentiality gate — refuse before any SQL is generated.
        # This is a fast first line; the SQL guardrail below is the deterministic backstop.
        if route == "restricted":
            reason = message or ("This request asks for restricted personal data, which the "
                                 "data-access policy does not allow.")
            with span(trace, "policy_precheck") as m:
                m["detail"] = "blocked early: restricted-data intent"
                m["meta"] = {"decision": "block", "rule": "restricted_intent"}
            guardrail = guardrails.Decision("block", "restricted_intent", reason)
            resp = self._response(question, trace, route="restricted", status="blocked",
                                  guardrail=guardrail, message=reason)
            resp["turn_id"] = memory.create_turn(
                session_id, question, route="restricted", guardrail_decision="block",
                guardrail_reason=reason, status="blocked", final_answer=reason,
                trace=trace.to_list())
            return resp

        # Branch B: needs SQL.
        sql, explanation = self._generate(question, session_id, trace)
        guardrail = self._guardrail(sql, trace)

        if guardrail.decision == "block":
            resp = self._response(question, trace, sql=sql, explanation=explanation,
                                  guardrail=guardrail, status="blocked",
                                  message=guardrail.reason, error=None)
            resp["turn_id"] = memory.create_turn(
                session_id, question, route="sql", generated_sql=sql,
                guardrail_decision="block", guardrail_reason=guardrail.reason,
                status="blocked", final_answer=explanation, trace=trace.to_list())
            return resp

        if guardrail.decision == "review":
            # Human-in-the-loop pause: store and return without executing.
            with span(trace, "await_human") as m:
                m["detail"] = "flagged for human review"
            turn_id = memory.create_turn(
                session_id, question, route="sql", generated_sql=sql,
                guardrail_decision="review", guardrail_reason=guardrail.reason,
                status="needs_approval", final_answer=explanation, trace=trace.to_list())
            return self._response(question, trace, sql=sql, explanation=explanation,
                                  guardrail=guardrail, status="needs_approval",
                                  needs_approval=True, message=guardrail.reason, turn_id=turn_id)

        # allow -> execute (with repair)
        final_sql, final_expl, columns, rows, error = self._execute_with_repair(
            question, sql, explanation, trace)
        answer, faithfulness = "", None
        if not error and rows and settings.summarize_results:
            answer, faithfulness = self._summarize(question, columns, rows, trace)
        turn_id = memory.create_turn(
            session_id, question, route="sql", generated_sql=final_sql,
            guardrail_decision="allow", guardrail_reason=guardrail.reason,
            status="error" if error else "completed",
            query_result={"columns": columns, "rows": rows},
            final_answer=answer or final_expl,
            row_count=len(rows), error=error, trace=trace.to_list())
        return self._response(question, trace, sql=final_sql, explanation=final_expl,
                              columns=columns, rows=rows, error=error, guardrail=guardrail,
                              status="error" if error else "completed", turn_id=turn_id,
                              answer=answer, faithfulness=faithfulness)

    def review(self, turn_id, decision, modified_sql=None, reason=""):
        """Resume a turn that was paused for human review."""
        turn = memory.get_turn(turn_id)
        if not turn:
            return None
        if turn.get("status") != "needs_approval":
            # Already decided; return its current state rather than re-running.
            return self._replay(turn, note="This query was already reviewed.")

        question = turn["question"]
        session_id = turn["session_id"]
        trace = Trace()
        with span(trace, "human_decision") as m:
            m["detail"] = f"{decision}" + (f": {reason}" if reason else "")
            m["meta"] = {"decision": decision, "reason": reason}

        if decision == "reject":
            memory.update_turn(turn_id, status="rejected", human_approval="reject",
                               human_reason=reason, trace=(turn.get("trace") or []) + trace.to_list())
            return self._response(question, trace, route="sql", sql=turn["generated_sql"],
                                  status="rejected", message=reason or "Rejected by reviewer.",
                                  turn_id=turn_id)

        # approve or modify -> pick the SQL to run, re-check it, then execute.
        sql = modified_sql.strip() if (decision == "modify" and modified_sql) else turn["generated_sql"]
        recheck = self._guardrail(sql, trace)
        if recheck.decision == "block":
            memory.update_turn(turn_id, status="blocked", human_approval=decision,
                               human_reason=reason, guardrail_decision="block",
                               guardrail_reason=recheck.reason)
            return self._response(question, trace, sql=sql, guardrail=recheck,
                                  status="blocked", message=recheck.reason, turn_id=turn_id)

        final_sql, final_expl, columns, rows, error = self._execute_with_repair(
            question, sql, turn.get("final_answer", ""), trace)
        answer, faithfulness = "", None
        if not error and rows and settings.summarize_results:
            answer, faithfulness = self._summarize(question, columns, rows, trace)
        full_trace = (turn.get("trace") or []) + trace.to_list()
        memory.update_turn(
            turn_id, generated_sql=final_sql, human_approval=decision, human_reason=reason,
            guardrail_decision="allow", status="error" if error else "completed",
            query_result={"columns": columns, "rows": rows}, final_answer=answer or final_expl,
            row_count=len(rows), error=error, trace=full_trace)
        resp = self._response(question, trace, sql=final_sql, explanation=final_expl,
                              columns=columns, rows=rows, error=error, guardrail=recheck,
                              status="error" if error else "completed", turn_id=turn_id,
                              answer=answer, faithfulness=faithfulness)
        resp["trace"] = full_trace
        return resp

    def _replay(self, turn, note=""):
        result = turn.get("query_result") or {}
        return {
            "question": turn["question"], "route": turn.get("route", "sql"),
            "status": turn.get("status"), "needs_approval": False, "message": note,
            "sql": turn.get("generated_sql", ""), "explanation": "",
            "answer": turn.get("final_answer", ""), "faithfulness": None,
            "columns": result.get("columns", []), "rows": result.get("rows", []),
            "row_count": turn.get("row_count") or 0, "error": turn.get("error"),
            "guardrail_decision": turn.get("guardrail_decision"),
            "guardrail_rule": None,
            "guardrail_reason": turn.get("guardrail_reason"),
            "provider": self.llm.name, "trace": turn.get("trace") or [],
            "total_ms": 0.0, "turn_id": turn["id"],
        }
