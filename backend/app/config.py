import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _get(key, default=None):
    v = os.getenv(key)
    return v if v not in (None, "") else default


class Settings:
    # LLM provider: mock | anthropic | openai
    llm_provider = _get("LLM_PROVIDER", "mock")
    anthropic_api_key = _get("ANTHROPIC_API_KEY", "")
    anthropic_model = _get("ANTHROPIC_MODEL", "claude-sonnet-5")
    openai_api_key = _get("OPENAI_API_KEY", "")
    openai_model = _get("OPENAI_MODEL", "gpt-4o-mini")

    # Guardrails
    max_result_rows = int(_get("MAX_RESULT_ROWS", "200"))  # above this a query needs human review
    max_joins = int(_get("MAX_JOINS", "2"))                # more JOINs than this needs human review

    # Orchestration
    agent_mode = _get("AGENT_MODE", "single")              # single | multi (multi = specialist council)
    max_repairs = int(_get("MAX_REPAIRS", "1"))            # auto-retries after a SQL execution error
    summarize_results = _get("SUMMARIZE_RESULTS", "true").lower() == "true"
    faithfulness_threshold = float(_get("FAITHFULNESS_THRESHOLD", "0.7"))
    summarize_max_rows = int(_get("SUMMARIZE_MAX_ROWS", "40"))  # rows shown to the summarizer

    # Memory / context
    memory_summarize_after_turns = int(_get("MEMORY_SUMMARIZE_AFTER_TURNS", "20"))

    # CORS
    allowed_origins = _get("ALLOWED_ORIGINS", "*")

    # Paths
    operations_db_path = _get(
        "OPERATIONS_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "operations.db")
    )
    memory_db_path = _get(
        "MEMORY_DB_PATH", os.path.join(os.path.dirname(__file__), "..", ".data", "memory.db")
    )


settings = Settings()
