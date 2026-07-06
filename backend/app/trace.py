"""Lightweight execution trace: each orchestration step is recorded with its
name, a short human-readable detail, how long it took, and any structured
metadata (e.g. the guardrail decision). The whole trace is returned to the UI
so every request is auditable step by step."""
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict


@dataclass
class Step:
    name: str
    detail: str = ""
    duration_ms: float = 0.0
    meta: dict = field(default_factory=dict)


class Trace:
    def __init__(self):
        self.steps = []

    def to_list(self):
        return [asdict(s) for s in self.steps]

    @property
    def total_ms(self):
        return round(sum(s.duration_ms for s in self.steps), 1)


@contextmanager
def span(trace, name, detail="", **meta):
    """Time a block of work and append it to the trace as one Step.

    Usage:
        with span(trace, "generate_sql") as m:
            sql = ...
            m["detail"] = sql            # optional: fill in details as you go
    """
    t0 = time.perf_counter()
    holder = {"detail": detail, "meta": dict(meta)}
    try:
        yield holder
    finally:
        trace.steps.append(
            Step(
                name=name,
                detail=holder.get("detail", detail),
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                meta=holder.get("meta", {}),
            )
        )
