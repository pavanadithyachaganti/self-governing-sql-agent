"""The safety layer. Every generated SQL query passes through here before it is
allowed anywhere near the database. Each rule has a name and a human-readable
reason so a decision can always be explained and logged.

Three decisions, in order of severity:
  block  - never run this (destructive, non-SELECT, multiple statements).
  review - suspicious but not forbidden; pause and ask a human (complex joins,
           very large result sets). This is the human-in-the-loop trigger.
  allow  - passed every check, safe to execute.
"""
import re
from dataclasses import dataclass

# DML/DDL verbs that must never appear in a read query. These are not SQLite
# scalar functions, so matching them as whole words does not create false
# positives on legitimate SELECTs (unlike e.g. REPLACE, which is a function).
_DESTRUCTIVE = re.compile(
    r"\b(drop|delete|insert|update|alter|truncate|attach|detach|grant|revoke|vacuum|reindex|replace\s+into)\b",
    re.I,
)
_LEADING_SELECT = re.compile(r"^\s*(select|with)\b", re.I)
_JOIN = re.compile(r"\bjoin\b", re.I)
_HAS_LIMIT = re.compile(r"\blimit\b", re.I)

# Restricted PII columns in the `workers` table. A query is blocked if it names
# any of these, or if it uses SELECT * / alias.* in a way that would expose them
# (i.e. selects all columns while the workers table is in play).
_RESTRICTED_COLUMNS = ("national_id", "home_address", "phone",
                       "medical_conditions", "monthly_salary_aed")
_SELECT_STAR = re.compile(r"select\s+\*|\b\w+\.\*", re.I)   # "SELECT *" or "w.*" (not COUNT(*))
_WORKERS_TABLE = re.compile(r"\bworkers\b", re.I)
_AGGREGATES = ("avg", "sum", "min", "max", "count", "total")


@dataclass
class Decision:
    decision: str   # "allow" | "review" | "block"
    rule: str       # short machine name of the rule that fired
    reason: str     # human-readable explanation


def static_check(sql: str, max_joins: int) -> Decision:
    """Structural checks that need no database access."""
    stripped = (sql or "").strip().rstrip(";").strip()

    if not stripped:
        return Decision("block", "empty_query", "No SQL query was produced.")

    # A single statement only: a semicolon in the middle means a second,
    # possibly hidden, statement was smuggled in.
    if ";" in stripped:
        return Decision("block", "multiple_statements",
                        "Only a single statement is allowed; multiple statements were detected.")

    if not _LEADING_SELECT.match(stripped):
        return Decision("block", "non_select",
                        "Only read-only SELECT queries are allowed.")

    if _DESTRUCTIVE.search(stripped):
        return Decision("block", "destructive_keyword",
                        "Query contains a data-modifying or destructive keyword.")

    joins = len(_JOIN.findall(stripped))
    if joins > max_joins:
        return Decision("review", "join_complexity",
                        f"Query uses {joins} joins, above the safe threshold of {max_joins}; "
                        f"needs review before running.")

    return Decision("allow", "passed", "Passed all structural safety checks.")


def _uses_restricted_column_raw(lowered, column):
    """True if the restricted column appears anywhere OUTSIDE an aggregate call.
    Aggregated use (AVG/SUM/MIN/MAX/COUNT over the column) exposes only a
    statistic, not any individual's value, so it is permitted; a bare selection
    or a WHERE/filter on the raw value is not."""
    total = len(re.findall(rf"\b{column}\b", lowered))
    if total == 0:
        return False
    agg = "|".join(_AGGREGATES)
    # occurrences of the column inside an aggregate function: agg( ... column ... )
    aggregated = len(re.findall(rf"(?:{agg})\s*\([^()]*\b{column}\b[^()]*\)", lowered))
    return total > aggregated


def sensitivity_check(sql: str, restricted_columns=_RESTRICTED_COLUMNS) -> Decision:
    """Confidentiality guardrail. Refuses to expose restricted PII columns from
    the workers table at the row level, while allowing aggregate statistics over
    them (e.g. average salary by site). Also catches blanket SELECT * that would
    sweep the columns in indirectly.

    `restricted_columns` is the set still off-limits for the current role; a role
    permitted to see a column passes an empty/reduced set, so the same query can
    be allowed for HR and blocked for an analyst."""
    lowered = (sql or "").lower()

    if not restricted_columns:
        return Decision("allow", "no_pii", "Role is permitted to access personal data.")

    raw = [c for c in restricted_columns if _uses_restricted_column_raw(lowered, c)]
    if raw:
        shown = ", ".join(raw)
        return Decision("block", "restricted_pii",
                        f"Query exposes restricted personal data at the row level ({shown}); "
                        f"blocked by the data-access policy for this role. Aggregate statistics "
                        f"are allowed.")

    if _WORKERS_TABLE.search(lowered) and _SELECT_STAR.search(lowered):
        return Decision("block", "restricted_pii",
                        "Query selects all columns from the workers table, which would expose "
                        "restricted personal data not permitted for this role.")

    return Decision("allow", "no_pii", "No restricted columns exposed at the row level.")


def size_check(row_estimate, max_result_rows: int, sql: str) -> Decision:
    """Result-size check, using a COUNT estimate the caller computed. A query
    that would return more than the threshold and has no LIMIT is flagged for
    review rather than dumping a huge result set."""
    if row_estimate is None:
        return Decision("allow", "size_unknown", "Could not estimate result size; allowed.")
    if row_estimate > max_result_rows and not _HAS_LIMIT.search(sql or ""):
        return Decision("review", "large_result",
                        f"Query would return about {row_estimate} rows, above the {max_result_rows}-row "
                        f"threshold, and has no LIMIT; needs review before running.")
    return Decision("allow", "size_ok", f"Result size ({row_estimate} rows) within limits.")


def combine(*decisions: Decision) -> Decision:
    """Pick the most severe decision. block > review > allow."""
    order = {"block": 2, "review": 1, "allow": 0}
    return max(decisions, key=lambda d: order[d.decision])
