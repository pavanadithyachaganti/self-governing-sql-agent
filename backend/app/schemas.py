from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = "default"


class QueryResponse(BaseModel):
    question: str
    route: str                       # sql | clarify | chit_chat
    status: str                      # completed | blocked | needs_approval | rejected | error | clarify | chit_chat
    needs_approval: bool = False
    message: str = ""                # clarifying question, chit-chat reply, or guardrail/refusal reason
    sql: str = ""
    explanation: str = ""
    answer: str = ""                 # grounded natural-language summary of the result rows
    faithfulness: Optional[float] = None  # 0-1 support of the answer by the rows
    columns: List[str] = []
    rows: List[List[Any]] = []
    row_count: int = 0
    error: Optional[str] = None
    guardrail_decision: Optional[str] = None
    guardrail_rule: Optional[str] = None
    guardrail_reason: Optional[str] = None
    provider: str
    trace: List[Dict[str, Any]] = []
    total_ms: float = 0.0
    turn_id: Optional[int] = None


class ReviewRequest(BaseModel):
    turn_id: int
    decision: str                    # approve | reject | modify
    modified_sql: Optional[str] = None
    reason: Optional[str] = ""


class HistoryTurn(BaseModel):
    id: int
    ts: float
    session_id: Optional[str] = None
    question: str
    route: Optional[str] = None
    sql: Optional[str] = None
    guardrail_decision: Optional[str] = None
    human_approval: Optional[str] = None
    status: Optional[str] = None
    row_count: Optional[int] = None
    error: Optional[str] = None


class HistoryResponse(BaseModel):
    turns: List[HistoryTurn]
