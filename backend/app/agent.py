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
import time
from concurrent.futures import ThreadPoolExecutor
from .db import SCHEMA_DESCRIPTION, estimate_rows, run_query
from .llm import get_llm, complete_json
from . import memory, guardrails, policy
from .config import settings
from .trace import Trace, span, Step

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

MEMORY_SUMMARY_SYSTEM = """STEP=memory_summary
You maintain a running summary of a data-analysis conversation. Given the
previous summary (may be empty) and several new earlier turns (each a question
and the SQL that answered it), produce an updated, compact summary that preserves
what was asked about and any entities/filters that later questions might refer back
to. Keep it under 120 words.
Respond with strict JSON only, no markdown: {"summary": "<updated summary>"}
"""

SCHEMA_EXPERT_SYSTEM = f"""STEP=schema_expert
You are a database schema expert. Given a question, identify which tables and
columns are relevant and any joins needed to answer it. Do not write SQL.

Schema:
{SCHEMA_DESCRIPTION}

Respond with strict JSON only, no markdown: {{"advice": "<tables, columns, and joins to use>"}}
"""

POLICY_EXPERT_SYSTEM = """STEP=policy_expert
You are a data-access policy expert. Given a question and the requester's role
and column restrictions, advise how to answer it within policy: which columns to
avoid, whether to aggregate a sensitive column instead of exposing it row by row,
or whether it cannot be answered at all. Do not write SQL.
Respond with strict JSON only, no markdown: {"advice": "<policy guidance>"}
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

    def _expert(self, system, user):
        """One specialist call, timed so the council can report each duration."""
        t0 = time.perf_counter()
        parsed = complete_json(self.llm, system, user)
        return (parsed.get("advice") or "").strip(), round((time.perf_counter() - t0) * 1000, 1)

    def _council(self, question, role, trace):
        """Multi-agent step: a schema expert and a policy expert are consulted
        CONCURRENTLY (real parallel I/O via threads); a supervisor folds their
        advice into the SQL generation prompt. Deterministic guardrails still
        enforce afterward — the experts advise, they do not decide."""
        restricted = policy.restricted_for(role)
        policy_ctx = (f"Requester role: {role}. Columns this role may NOT see at the row level: "
                      f"{', '.join(restricted) if restricted else 'none (full access)'}.")
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_schema = ex.submit(self._expert, SCHEMA_EXPERT_SYSTEM, f"Question: {question}")
            f_policy = ex.submit(self._expert, POLICY_EXPERT_SYSTEM,
                                 f"{policy_ctx}\n\nQuestion: {question}")
            schema_advice, ms_s = f_schema.result()
            policy_advice, ms_p = f_policy.result()
        wall_ms = round((time.perf_counter() - t0) * 1000, 1)
        # Both ran in parallel: record each with its own duration, noting the
        # wall-clock time is roughly the slower one, not the sum.
        trace.steps.append(Step(name="schema_expert", detail=schema_advice[:200] or "(none)",
                                duration_ms=ms_s, meta={"parallel_group": "council"}))
        trace.steps.append(Step(name="policy_expert", detail=policy_advice[:200] or "(none)",
                                duration_ms=ms_p, meta={"parallel_group": "council", "wall_ms": wall_ms}))
        return "\n".join(x for x in (
            f"Schema expert: {schema_advice}" if schema_advice else "",
            f"Policy expert: {policy_advice}" if policy_advice else "",
        ) if x)

    def _summarize_memory(self, prev_summary, new_turns):
        turns_text = "\n".join(f"Q: {t['question']}\nSQL: {t['sql']}" for t in new_turns)
        user = f"Previous summary:\n{prev_summary or '(none)'}\n\nNew earlier turns:\n{turns_text}"
        parsed = complete_json(self.llm, MEMORY_SUMMARY_SYSTEM, user)
        return (parsed.get("summary") or prev_summary or "").strip()

    def _recall(self, session_id, trace):
        """Build conversation context. Recent turns are kept verbatim; once a
        session grows past the window, older turns are folded into a running
        summary (rebuilt only when new older turns appear, so it isn't redone
        every request)."""
        window = settings.memory_recent_window
        turns = memory.session_turns(session_id)          # oldest first
        if len(turns) <= window:
            return "\n".join(f"Q: {t['question']}\nSQL: {t['sql']}" for t in turns)

        older, recent = turns[:-window], turns[-window:]
        max_older_id = max(t["id"] for t in older)
        cached = memory.get_session_summary(session_id)
        if not cached or (cached["up_to_turn_id"] or 0) < max_older_id:
            with span(trace, "summarize_memory") as m:
                prev = cached["summary"] if cached else ""
                cutoff = cached["up_to_turn_id"] if cached else 0
                new_turns = [t for t in older if t["id"] > (cutoff or 0)]
                summary = self._summarize_memory(prev, new_turns)
                memory.upsert_session_summary(session_id, summary, max_older_id)
                m["detail"] = f"folded {len(new_turns)} older turn(s) into the running summary"
                m["meta"] = {"older_turns": len(older), "recent_kept": len(recent)}
        else:
            summary = cached["summary"]

        recent_text = "\n".join(f"Q: {t['question']}\nSQL: {t['sql']}" for t in recent)
        return f"Summary of earlier conversation:\n{summary}\n\nRecent turns:\n{recent_text}"

    def _generate(self, question, session_id, trace, role="analyst", advice=""):
        history = self._recall(session_id, trace)
        with span(trace, "generate_sql") as m:
            parts = []
            if advice:
                parts.append(f"Specialist guidance:\n{advice}")
            if history:
                parts.append(f"Recent conversation:\n{history}")
            allowed = policy.ROLE_ALLOWED.get(role, set())
            if allowed:
                # Tell the generator the caller is authorized, so it does not
                # self-censor a query the access policy actually permits.
                parts.append(f"Authorization: the requester's role '{role}' is permitted to access "
                             f"these otherwise-restricted columns: {', '.join(sorted(allowed))}. "
                             f"Write the SQL they asked for; do not refuse on privacy grounds for "
                             f"these columns.")
            parts.append(f"New question: {question}" if history else question)
            user = "\n\n".join(parts)
            parsed = complete_json(self.llm, GENERATE_SYSTEM, user)
            sql = (parsed.get("sql") or "").strip()
            explanation = parsed.get("explanation", "")
            m["detail"] = sql or "(no query produced)"
            return sql, explanation

    def _guardrail(self, sql, trace, role="analyst"):
        with span(trace, "guardrail") as m:
            static = guardrails.static_check(sql, settings.max_joins)
            decision = static
            meta = {"role": role}
            if static.decision != "block":
                sensitivity = guardrails.sensitivity_check(sql, policy.restricted_for(role))
                est = estimate_rows(sql)
                size = guardrails.size_check(est, settings.max_result_rows, sql)
                decision = guardrails.combine(static, sensitivity, size)
                meta["row_estimate"] = est
            m["detail"] = f"{decision.decision}: {decision.rule} (role={role})"
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

    def run(self, question, session_id="default", role="analyst", mode=None):
        role = policy.normalize(role)
        mode = mode or settings.agent_mode
        trace = Trace()
        route, message = self._plan(question, trace)

        # Branch A: no database access needed.
        if route in ("clarify", "chit_chat"):
            resp = self._response(question, trace, route=route, status=route, message=message)
            resp["turn_id"] = memory.create_turn(
                session_id, question, route=route, status=route,
                final_answer=message, trace=trace.to_list(), role=role)
            return resp

        # Branch A': early confidentiality gate. Refuse before any SQL is generated
        # ONLY when this role has no personal-data access at all; roles that can see
        # some PII fall through to the role-aware SQL guardrail, which enforces the
        # exact per-column policy.
        if route == "restricted":
            if not policy.can_see_any_pii(role):
                reason = message or ("This request asks for restricted personal data, which the "
                                     f"'{role}' role is not allowed to access.")
                with span(trace, "policy_precheck") as m:
                    m["detail"] = f"blocked early: restricted-data intent (role={role})"
                    m["meta"] = {"decision": "block", "rule": "restricted_intent", "role": role}
                guardrail = guardrails.Decision("block", "restricted_intent", reason)
                resp = self._response(question, trace, route="restricted", status="blocked",
                                      guardrail=guardrail, message=reason)
                resp["turn_id"] = memory.create_turn(
                    session_id, question, route="restricted", guardrail_decision="block",
                    guardrail_reason=reason, status="blocked", final_answer=reason,
                    trace=trace.to_list(), role=role)
                return resp
            route = "sql"  # privileged role: let the SQL guardrail decide per column

        # Branch B: needs SQL. In multi-agent mode, consult the specialist council first.
        advice = self._council(question, role, trace) if mode == "multi" else ""
        sql, explanation = self._generate(question, session_id, trace, role, advice)
        guardrail = self._guardrail(sql, trace, role)

        if guardrail.decision == "block":
            resp = self._response(question, trace, sql=sql, explanation=explanation,
                                  guardrail=guardrail, status="blocked",
                                  message=guardrail.reason, error=None)
            resp["turn_id"] = memory.create_turn(
                session_id, question, route="sql", generated_sql=sql,
                guardrail_decision="block", guardrail_reason=guardrail.reason,
                status="blocked", final_answer=explanation, trace=trace.to_list(), role=role)
            return resp

        if guardrail.decision == "review":
            # Human-in-the-loop pause: store and return without executing.
            with span(trace, "await_human") as m:
                m["detail"] = "flagged for human review"
            turn_id = memory.create_turn(
                session_id, question, route="sql", generated_sql=sql,
                guardrail_decision="review", guardrail_reason=guardrail.reason,
                status="needs_approval", final_answer=explanation, trace=trace.to_list(), role=role)
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
            row_count=len(rows), error=error, trace=trace.to_list(), role=role)
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
        role = policy.normalize(turn.get("role") or "analyst")
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
