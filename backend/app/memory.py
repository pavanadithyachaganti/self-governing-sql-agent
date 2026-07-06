"""Append-only conversation memory. Each turn is one question -> SQL -> result
cycle. guardrail_decision and human_approval are populated starting week 2;
for now every turn is written with them set to NULL."""
import os
import time
import json
import sqlite3
from .config import settings


def _conn():
    os.makedirs(os.path.dirname(settings.memory_db_path), exist_ok=True)
    c = sqlite3.connect(settings.memory_db_path)
    c.execute(
        """CREATE TABLE IF NOT EXISTS turns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            session_id TEXT,
            question TEXT,
            generated_sql TEXT,
            guardrail_decision TEXT,
            human_approval TEXT,
            query_result TEXT,
            final_answer TEXT,
            row_count INTEGER,
            error TEXT
        )"""
    )
    return c


def save_turn(session_id, question, generated_sql, query_result, final_answer,
              row_count=None, error=None, guardrail_decision=None, human_approval=None):
    c = _conn()
    c.execute(
        """INSERT INTO turns(ts, session_id, question, generated_sql, guardrail_decision,
                              human_approval, query_result, final_answer, row_count, error)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (time.time(), session_id, question, generated_sql, guardrail_decision,
         human_approval, json.dumps(query_result), final_answer, row_count, error),
    )
    turn_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit()
    c.close()
    return turn_id


def recent_turns(session_id=None, limit=20):
    c = _conn()
    if session_id:
        rows = c.execute(
            """SELECT id, ts, session_id, question, generated_sql, guardrail_decision,
                      human_approval, row_count, error
               FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
    else:
        rows = c.execute(
            """SELECT id, ts, session_id, question, generated_sql, guardrail_decision,
                      human_approval, row_count, error
               FROM turns ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    c.close()
    cols = ["id", "ts", "session_id", "question", "sql", "guardrail_decision",
            "human_approval", "row_count", "error"]
    return [dict(zip(cols, r)) for r in rows]


def context_for_prompt(session_id, max_turns=5):
    """Short recent-history text fed back to the LLM as conversation context.
    Full summarization of older turns lands in week 2 alongside guardrails."""
    turns = recent_turns(session_id, limit=max_turns)[::-1]
    if not turns:
        return ""
    lines = []
    for t in turns:
        lines.append(f"Q: {t['question']}\nSQL: {t['sql']}")
    return "\n".join(lines)
