from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .schemas import QueryRequest, QueryResponse, HistoryResponse
from .agent import SQLAgent
from . import memory

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
    return {"status": "ok", "provider": settings.llm_provider}


@app.post("/api/query", response_model=QueryResponse)
def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(400, "Question is empty.")
    result = get_agent().run(req.question, session_id=req.session_id or "default")
    return QueryResponse(**result)


@app.get("/api/history", response_model=HistoryResponse)
def history(session_id: Optional[str] = None, limit: int = 20):
    return HistoryResponse(turns=memory.recent_turns(session_id, limit))
