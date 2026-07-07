"""Role-based access policy. Which restricted columns a request may see depends
on the caller's role. The guardrail reads this to decide what is "restricted"
for *this* request, so the same question gives different answers to different
roles — an analyst is refused worker salaries, HR is not.

This is intentionally a small, declarative table rather than a database of
users: the point is to show the guardrail becoming an access-control system,
not to build an auth stack."""

# Every column the confidentiality guardrail can protect.
ALL_RESTRICTED = ("national_id", "home_address", "phone",
                  "medical_conditions", "monthly_salary_aed")

# role -> the restricted columns that role is ALLOWED to see at the row level.
ROLE_ALLOWED = {
    "analyst": set(),                              # no personal data
    "safety_officer": {"medical_conditions"},      # health data, for safety follow-up
    "hr_admin": set(ALL_RESTRICTED),               # full personnel access
}

DEFAULT_ROLE = "analyst"


def normalize(role):
    return role if role in ROLE_ALLOWED else DEFAULT_ROLE


def restricted_for(role):
    """The columns still off-limits for this role (what the guardrail enforces)."""
    return tuple(c for c in ALL_RESTRICTED if c not in ROLE_ALLOWED[normalize(role)])


def can_see_any_pii(role):
    """Whether the role can see any personal data at all — used by the early
    planner gate to decide whether to refuse up front or let the query proceed
    to the (role-aware) SQL guardrail."""
    return bool(ROLE_ALLOWED[normalize(role)])


def roles():
    return list(ROLE_ALLOWED.keys())
