# Agent Orchestration System

An agent that turns natural language questions into safe, auditable queries over operational data. Framework-free, with SQL guardrails and human-in-the-loop review for anything destructive or ambiguous.

> **Status: Week 1.** The core text-to-SQL loop, synthetic dataset, and append-only memory are working end to end. Guardrails, human-in-the-loop review, and trace observability land in week 2. See [Roadmap](#roadmap).

## What this is

A natural-language interface over a synthetic industrial safety operations database: ask a question, an LLM generates a SQL query, the query runs against SQLite, and the result comes back. No LangChain, LangGraph, AutoGen, or CrewAI — the agent loop, provider abstraction, and memory are plain Python, same discipline as the self-correcting RAG repo.

Runs with zero API keys: a keyword-matched mock provider exercises the full pipeline offline, so the app, dataset, and memory log are fully demonstrable without a key.

## Dataset

Synthetic, generated with Faker (`backend/scripts/generate_data.py`), persisted to SQLite at `backend/data/operations.db`. ~6,900 rows across three tables, covering 6 sites and 120 workers over a 180-day window.

| Table | Rows | Grain |
| --- | --- | --- |
| `safety_incidents` | ~750 | one row per reported incident |
| `worker_vitals` | ~5,100 | one row per sensor reading |
| `operational_metrics` | 1,080 | one row per site per day |

Distributions are hand-tuned, not uniform: severity is skewed toward `low` (~55%) with `critical` rare (~3%); incident resolution status depends on how old the incident is (recent incidents skew `open`/`in_progress`, older ones skew `resolved`/`closed`); heart rate and body temperature are correlated with `activity_level`, with occasional heat-stress outliers; and `productivity_index` is negatively correlated with `incidents_reported` and `near_misses` for the same site-day.

Regenerate it any time:

```bash
cd backend
python scripts/generate_data.py
```

Published under the MIT license along with the rest of the repo — see [LICENSE](LICENSE).

## Quickstart

```bash
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/generate_data.py                        # builds data/operations.db
cp .env.example .env
uvicorn app.main:app --reload
```

That runs with `LLM_PROVIDER=mock`, so SQL comes from keyword rules rather than a real model, but the full loop — question, SQL generation, execution, memory logging — is live.

For real SQL generation, add a key to `.env`:

```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_key_here
```

or

```
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

Ask a question:

```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"question": "How many incidents are there by severity?"}'
```

### Verify it without the server

```bash
python scripts/smoke_test.py     # offline end-to-end check, no keys, no network
```

## How the loop works

```
question
   -> LLM generates a SELECT query against the schema
   -> execute against SQLite (read-only connection)
   -> results + explanation returned
   -> turn appended to the memory log (question, SQL, result, no guardrail/approval yet)
```

## Project structure

```
backend/
  app/
    config.py     settings from .env
    db.py         SQLite connection + schema description used in prompts
    llm.py        Anthropic / OpenAI / mock provider abstraction
    agent.py       text-to-SQL loop
    memory.py     append-only conversation log (SQLite)
    schemas.py    pydantic request/response models
    main.py       FastAPI app and endpoints
  data/
    operations.db   generated synthetic dataset (gitignored, regenerate with the script below)
  scripts/
    generate_data.py   synthetic dataset generator (Faker)
    smoke_test.py      offline end-to-end check
```

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/query` | run the agent on a natural-language question, return SQL + results |
| `GET /api/history` | recent conversation turns from the memory log |
| `GET /api/health` | active LLM provider |

## Roadmap

- **Week 2.** Guardrails layer (block destructive statements, cap join complexity, flag large result sets) with named rules and rejection reasons. Human-in-the-loop review UI (approve / reject with reason / modify SQL) feeding a decision log. Trace observability (per-step timing, guardrail decisions, human decisions).
- **Week 3.** Polish, deploy to Render + Vercel, smoke tests and eval scripts, public writeup alongside the RAG repo.

## What this demonstrates

Text-to-SQL agent design, provider abstraction, conversation memory, and synthetic dataset generation with realistic distributions, built without a framework doing the thinking.

Built by Pavan Adithya Chaganti · [LinkedIn](https://www.linkedin.com/in/pavan-adithya-chaganti-763840214)
