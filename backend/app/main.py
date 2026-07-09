import os
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .config import settings
from .schemas import QueryRequest, QueryResponse, ReviewRequest, HistoryResponse
from .agent import SQLAgent
from . import memory, policy

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="Agent Orchestration System")
_origins = (["*"] if settings.allowed_origins.strip() == "*"
            else [o.strip() for o in settings.allowed_origins.split(",") if o.strip()])
app.add_middleware(
    CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"],
)

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = SQLAgent()
    return _agent


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "max_result_rows": settings.max_result_rows,
        "max_joins": settings.max_joins,
        "roles": policy.roles(),
        "agent_mode": settings.agent_mode,
    }


@app.post("/api/query", response_model=QueryResponse)
def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(400, "Question is empty.")
    result = get_agent().run(req.question, session_id=req.session_id or "default",
                             role=req.role or "analyst", mode=req.mode)
    return QueryResponse(**result)


@app.post("/api/review", response_model=QueryResponse)
def review(req: ReviewRequest):
    if req.decision not in ("approve", "reject", "modify"):
        raise HTTPException(400, "decision must be approve, reject, or modify.")
    result = get_agent().review(req.turn_id, req.decision,
                                modified_sql=req.modified_sql, reason=req.reason or "")
    if result is None:
        raise HTTPException(404, f"No turn with id {req.turn_id}.")
    return QueryResponse(**result)


@app.get("/api/history", response_model=HistoryResponse)
def history(session_id: Optional[str] = None, limit: int = 20):
    return HistoryResponse(turns=memory.recent_turns(session_id, limit))


@app.get("/api/schema")
def schema():
    """The operational database's shape, for the UI's context sidebar so users
    know what they can ask about. Restricted columns are flagged."""
    from .db import get_connection
    restricted = set(policy.ALL_RESTRICTED)
    conn = get_connection()
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    tables = []
    for t in names:  # names come from sqlite_master, not user input
        cols = [{"name": row[1], "type": row[2], "restricted": row[1] in restricted}
                for row in conn.execute(f"PRAGMA table_info({t})")]
        try:
            rows = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            rows = None
        tables.append({"name": t, "rows": rows, "columns": cols})
    conn.close()
    return {"tables": tables, "restricted": sorted(restricted)}


@app.get("/api/stats")
def stats():
    return memory.stats()


@app.post("/api/eval")
def run_eval():
    from . import evals
    return evals.run(agent=get_agent())


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
