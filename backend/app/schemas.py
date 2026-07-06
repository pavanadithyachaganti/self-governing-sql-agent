from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = "default"


class QueryResponse(BaseModel):
    question: str
    sql: str
    explanation: str
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    error: Optional[str] = None
    provider: str
    total_ms: float


class HistoryTurn(BaseModel):
    id: int
    ts: float
    session_id: str
    question: str
    sql: str
    guardrail_decision: Optional[str] = None
    human_approval: Optional[str] = None
    row_count: Optional[int] = None
    error: Optional[str] = None


class HistoryResponse(BaseModel):
    turns: List[HistoryTurn]
