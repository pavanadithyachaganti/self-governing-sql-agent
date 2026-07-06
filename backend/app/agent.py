"""Naive text-to-SQL loop: question in, SQL out, execute, results back.
No guardrails and no human-in-the-loop yet -- those land in week 2."""
import time
import sqlite3
from .db import get_connection, SCHEMA_DESCRIPTION
from .llm import get_llm, complete_json
from . import memory

SYSTEM_PROMPT = f"""You are a SQL analyst for an industrial safety operations database.
Translate the user's question into a single read-only SQLite SELECT query.

Schema:
{SCHEMA_DESCRIPTION}

Rules:
- Only ever write SELECT queries. Never write, alter, or delete data.
- Use only the tables and columns listed above.
- Prefer explicit column lists over SELECT * when the question asks for an aggregate.
- Respond with strict JSON only, no markdown fences: {{"sql": "<query>", "explanation": "<one sentence>"}}
"""


class SQLAgent:
    def __init__(self):
        self.llm = get_llm()

    def _build_user_prompt(self, question, session_id):
        history = memory.context_for_prompt(session_id)
        if history:
            return f"Recent conversation:\n{history}\n\nNew question: {question}"
        return question

    def run(self, question, session_id="default"):
        t0 = time.perf_counter()
        user_prompt = self._build_user_prompt(question, session_id)
        parsed = complete_json(self.llm, SYSTEM_PROMPT, user_prompt)
        sql = (parsed.get("sql") or "").strip()
        explanation = parsed.get("explanation", "")

        columns, rows, error = [], [], None
        if sql:
            try:
                conn = get_connection()
                cur = conn.execute(sql)
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = [list(r) for r in cur.fetchall()]
                conn.close()
            except sqlite3.Error as e:
                error = str(e)
        else:
            error = "Model did not return a SQL query."

        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        result = {
            "question": question,
            "sql": sql,
            "explanation": explanation,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "error": error,
            "provider": self.llm.name,
            "total_ms": total_ms,
        }
        memory.save_turn(
            session_id=session_id,
            question=question,
            generated_sql=sql,
            query_result={"columns": columns, "rows": rows},
            final_answer=explanation,
            row_count=len(rows),
            error=error,
        )
        return result
