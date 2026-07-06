import re
import json
import time
import httpx
from .config import settings


def _post_with_retry(url, payload, headers=None, timeout=60, max_attempts=5):
    """POST with exponential backoff on 429 rate limits, honoring Retry-After."""
    r = None
    for attempt in range(max_attempts):
        r = httpx.post(url, json=payload, headers=headers or {}, timeout=timeout)
        if r.status_code == 429 and attempt < max_attempts - 1:
            ra = r.headers.get("retry-after", "")
            wait = float(ra) if ra.replace(".", "", 1).isdigit() else 2 ** attempt
            time.sleep(min(wait, 30))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


class LLMError(Exception):
    pass


_MOCK_RULES = [
    (re.compile(r"critical.*incident|incident.*critical", re.I),
     "SELECT * FROM safety_incidents WHERE severity = 'critical' ORDER BY date DESC;"),
    (re.compile(r"severity", re.I),
     "SELECT severity, COUNT(*) AS count FROM safety_incidents GROUP BY severity ORDER BY count DESC;"),
    (re.compile(r"incident.*type|type.*incident", re.I),
     "SELECT incident_type, COUNT(*) AS count FROM safety_incidents GROUP BY incident_type ORDER BY count DESC;"),
    (re.compile(r"open|unresolved", re.I),
     "SELECT * FROM safety_incidents WHERE resolution_status IN ('open', 'in_progress') ORDER BY date DESC;"),
    (re.compile(r"heart rate|vitals|body temp", re.I),
     "SELECT worker_id, AVG(heart_rate) AS avg_heart_rate, AVG(body_temp) AS avg_body_temp "
     "FROM worker_vitals GROUP BY worker_id ORDER BY avg_heart_rate DESC LIMIT 20;"),
    (re.compile(r"productivity", re.I),
     "SELECT site_id, AVG(productivity_index) AS avg_productivity FROM operational_metrics "
     "GROUP BY site_id ORDER BY avg_productivity DESC;"),
    (re.compile(r"near miss", re.I),
     "SELECT site_id, SUM(near_misses) AS total_near_misses FROM operational_metrics "
     "GROUP BY site_id ORDER BY total_near_misses DESC;"),
    (re.compile(r"salary|salaries|pay|wage", re.I),
     "SELECT worker_id, full_name, monthly_salary_aed FROM workers ORDER BY monthly_salary_aed DESC;"),
    (re.compile(r"national id|emirates id|passport|address|phone|medical|health condition", re.I),
     "SELECT worker_id, full_name, national_id, home_address, phone, medical_conditions FROM workers;"),
    (re.compile(r"\bworkers?\b|personnel|directory|roster", re.I),
     "SELECT * FROM workers;"),
    (re.compile(r"site|location", re.I),
     "SELECT site_id, COUNT(*) AS incidents FROM safety_incidents GROUP BY site_id ORDER BY incidents DESC;"),
]


class MockProvider:
    """Runs the full pipeline with no API key. A small keyword-matched rule set
    picks a plausible SQL query for common question shapes so the planner,
    guardrail, memory, and human-review layers are all exercisable offline.

    The mock inspects a marker the caller puts in the system prompt to tell
    which orchestration step is asking (STEP=plan / generate / repair)."""

    name = "mock"

    def complete(self, system, user, temperature=0.0, json_mode=False):
        if not json_mode:
            return "[mock] set LLM_PROVIDER=anthropic or openai with a key for real answers."
        sys_l = system.lower()
        question = user.strip().split("\n")[-1]

        if "step=plan" in sys_l:
            return self._plan(question)
        if "step=repair" in sys_l:
            # The mock can't truly repair; return a safe fallback query.
            return json.dumps({
                "sql": "SELECT COUNT(*) AS total_incidents FROM safety_incidents;",
                "explanation": "Mock repair: substituted a safe fallback query.",
            })
        return self._generate(question)

    def _plan(self, question):
        q = question.lower()
        if any(g in q for g in ["hello", "hi ", "thanks", "thank you", "who are you", "what can you do"]):
            return json.dumps({"route": "chit_chat",
                               "message": "I answer questions about the safety operations database. "
                                          "Try asking about incidents, worker vitals, or site productivity."})
        if any(p in q for p in ["salary", "salaries", "wage", "pay ", " pay", "national id", "emirates id",
                                "passport", "home address", "phone", "medical", "health condition"]):
            return json.dumps({"route": "restricted",
                               "message": "That request asks for restricted personal data "
                                          "(such as salary, national ID, contact details, or medical "
                                          "information), which the data-access policy does not allow."})
        if len(q.split()) <= 2:
            return json.dumps({"route": "clarify",
                               "message": "Could you say a bit more about what you'd like to know?"})
        return json.dumps({"route": "sql", "message": ""})

    def _generate(self, question):
        for pattern, sql in _MOCK_RULES:
            if pattern.search(question):
                return json.dumps({
                    "sql": sql,
                    "explanation": "Mock provider: matched a keyword rule for this question shape.",
                })
        return json.dumps({
            "sql": "SELECT COUNT(*) AS total_incidents FROM safety_incidents;",
            "explanation": "Mock provider: no keyword rule matched, returning a safe default query.",
        })


class AnthropicProvider:
    name = "anthropic"

    def __init__(self):
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY not set")
        self.model = settings.anthropic_model
        self.key = settings.anthropic_api_key

    def complete(self, system, user, temperature=0.0, json_mode=False):
        # temperature is deprecated on the latest Claude models, so it is omitted.
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        r = _post_with_retry(
            "https://api.anthropic.com/v1/messages",
            payload,
            headers={
                "x-api-key": self.key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        # The response may include non-text blocks (e.g. thinking); take the first text block.
        blocks = r.json().get("content", [])
        for block in blocks:
            if block.get("type") == "text":
                return block["text"]
        return ""


class OpenAIProvider:
    name = "openai"

    def __init__(self):
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY not set")
        self.model = settings.openai_model
        self.key = settings.openai_api_key

    def complete(self, system, user, temperature=0.0, json_mode=False):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = _post_with_retry(
            "https://api.openai.com/v1/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {self.key}"},
        )
        return r.json()["choices"][0]["message"]["content"]


_provider = None


def get_llm():
    global _provider
    if _provider is not None:
        return _provider
    p = settings.llm_provider
    try:
        if p == "anthropic":
            _provider = AnthropicProvider()
        elif p == "openai":
            _provider = OpenAIProvider()
        else:
            _provider = MockProvider()
    except LLMError:
        _provider = MockProvider()
    return _provider


def _safe_json(raw):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    # Neutral fallback: callers read fields with .get() and supply their own defaults.
    return {"_parse_error": True}


def complete_json(llm, system, user, temperature=0.0):
    raw = llm.complete(system, user, temperature=temperature, json_mode=True)
    return _safe_json(raw)
