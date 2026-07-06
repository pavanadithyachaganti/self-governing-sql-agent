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


def sensitivity_check(sql: str) -> Decision:
    """Confidentiality guardrail: refuse to expose restricted PII columns from
    the workers table. Catches both direct references (SELECT national_id ...)
    and blanket SELECT * that would sweep the columns in indirectly."""
    lowered = (sql or "").lower()

    named = [c for c in _RESTRICTED_COLUMNS if re.search(rf"\b{c}\b", lowered)]
    if named:
        shown = ", ".join(named)
        return Decision("block", "restricted_pii",
                        f"Query accesses restricted personal data ({shown}); blocked by the "
                        f"data-access policy.")

    if _WORKERS_TABLE.search(lowered) and _SELECT_STAR.search(lowered):
        return Decision("block", "restricted_pii",
                        "Query selects all columns from the workers table, which would expose "
                        "restricted personal data; blocked by the data-access policy.")

    return Decision("allow", "no_pii", "No restricted columns accessed.")


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
