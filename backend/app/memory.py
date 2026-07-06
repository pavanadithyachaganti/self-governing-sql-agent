"""Conversation memory and decision log. One row per turn. A turn is created
when a question arrives and may be updated later — for example a query that is
flagged for review is stored with status 'needs_approval' and no result, then
updated to 'completed' or 'rejected' once a human decides. Every guardrail
decision and human decision is recorded, so the log doubles as an audit trail."""
import os
import time
import json
import sqlite3
from .config import settings

# Full desired column set. _ensure_schema adds any that are missing so an older
# database file upgrades in place instead of breaking.
_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "ts": "REAL",
    "session_id": "TEXT",
    "question": "TEXT",
    "route": "TEXT",
    "generated_sql": "TEXT",
    "guardrail_decision": "TEXT",
    "guardrail_reason": "TEXT",
    "human_approval": "TEXT",
    "human_reason": "TEXT",
    "status": "TEXT",
    "query_result": "TEXT",
    "final_answer": "TEXT",
    "row_count": "INTEGER",
    "error": "TEXT",
    "trace": "TEXT",
}


def _conn():
    os.makedirs(os.path.dirname(settings.memory_db_path), exist_ok=True)
    c = sqlite3.connect(settings.memory_db_path)
    _ensure_schema(c)
    return c


def _ensure_schema(c):
    c.execute("CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY AUTOINCREMENT)")
    existing = {row[1] for row in c.execute("PRAGMA table_info(turns)")}
    for name, decl in _COLUMNS.items():
        if name not in existing:
            # PRIMARY KEY can't be added via ALTER; it already exists from CREATE above.
            if "PRIMARY KEY" in decl:
                continue
            c.execute(f"ALTER TABLE turns ADD COLUMN {name} {decl}")
    c.commit()


def create_turn(session_id, question, route="sql", generated_sql="",
                guardrail_decision=None, guardrail_reason=None, status="completed",
                query_result=None, final_answer="", row_count=None, error=None, trace=None,
                human_approval=None, human_reason=None):
    c = _conn()
    cur = c.execute(
        """INSERT INTO turns(ts, session_id, question, route, generated_sql,
               guardrail_decision, guardrail_reason, human_approval, human_reason,
               status, query_result, final_answer, row_count, error, trace)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (time.time(), session_id, question, route, generated_sql,
         guardrail_decision, guardrail_reason, human_approval, human_reason,
         status, json.dumps(query_result), final_answer, row_count, error,
         json.dumps(trace) if trace is not None else None),
    )
    turn_id = cur.lastrowid
    c.commit()
    c.close()
    return turn_id


def update_turn(turn_id, **fields):
    if not fields:
        return
    allowed = set(_COLUMNS) - {"id"}
    sets, values = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("query_result", "trace") and v is not None and not isinstance(v, str):
            v = json.dumps(v)
        sets.append(f"{k} = ?")
        values.append(v)
    if not sets:
        return
    values.append(turn_id)
    c = _conn()
    c.execute(f"UPDATE turns SET {', '.join(sets)} WHERE id = ?", values)
    c.commit()
    c.close()


def get_turn(turn_id):
    c = _conn()
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
    c.close()
    if not row:
        return None
    d = dict(row)
    for k in ("query_result", "trace"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


def recent_turns(session_id=None, limit=20):
    c = _conn()
    c.row_factory = sqlite3.Row
    if session_id:
        rows = c.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        out.append({
            "id": d["id"], "ts": d["ts"], "session_id": d["session_id"],
            "question": d["question"], "route": d["route"], "sql": d["generated_sql"],
            "guardrail_decision": d["guardrail_decision"], "human_approval": d["human_approval"],
            "status": d["status"], "row_count": d["row_count"], "error": d["error"],
        })
    return out


def stats():
    """Aggregate view over the whole decision log, for the dashboard."""
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM turns").fetchone()[0]

    def counts(col):
        rows = c.execute(
            f"SELECT COALESCE({col}, '(none)'), COUNT(*) FROM turns GROUP BY {col} ORDER BY 2 DESC"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    by_status = counts("status")
    by_decision = counts("guardrail_decision")
    by_route = counts("route")

    reviewed = c.execute(
        "SELECT COUNT(*) FROM turns WHERE human_approval IS NOT NULL"
    ).fetchone()[0]
    approved = c.execute(
        "SELECT COUNT(*) FROM turns WHERE human_approval IN ('approve', 'modify')"
    ).fetchone()[0]
    pending = c.execute(
        "SELECT COUNT(*) FROM turns WHERE status = 'needs_approval'"
    ).fetchone()[0]
    c.close()

    blocked = by_status.get("blocked", 0)
    return {
        "total": total,
        "blocked": blocked,
        "needs_approval": pending,
        "reviewed": reviewed,
        "approval_rate": round(approved / reviewed, 3) if reviewed else None,
        "by_status": by_status,
        "by_decision": by_decision,
        "by_route": by_route,
    }


def context_for_prompt(session_id, max_turns=5):
    """Short recent-history text fed back to the LLM so follow-up questions
    ('and for last month?') resolve against what was just asked."""
    turns = [t for t in recent_turns(session_id, limit=max_turns) if t["sql"]][::-1]
    if not turns:
        return ""
    return "\n".join(f"Q: {t['question']}\nSQL: {t['sql']}" for t in turns)
