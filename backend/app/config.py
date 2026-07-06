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

    # Guardrails (wired up in week 2; read here so config is stable across weeks)
    max_result_rows = int(_get("MAX_RESULT_ROWS", "200"))
    max_join_tables = int(_get("MAX_JOIN_TABLES", "2"))

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
