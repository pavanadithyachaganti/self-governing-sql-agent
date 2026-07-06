# Agent Orchestration System

An agent that turns natural language questions into safe, auditable queries over operational data. Framework-free, with SQL guardrails and human-in-the-loop review for anything destructive or ambiguous.

> **Status: ** The multi-step orchestrator, guardrail safety layer, human-in-the-loop review, and per-step trace are working end to end on top of the dataset and memory.

## What this is

A natural-language interface over a synthetic industrial safety operations database. A question is not blindly turned into SQL and run — it is **orchestrated** through named steps that make decisions: the question is routed, turned into SQL, checked by safety and confidentiality guardrails, held for a human if it looks risky, executed, repaired if it errors, and finally summarized into a grounded answer that is checked for faithfulness. Every step is traced, and every decision is logged. No LangChain, LangGraph, AutoGen, or CrewAI — the orchestrator, guardrails, provider abstraction, and memory are plain Python, same discipline as the self-correcting RAG repo.

## How the orchestration works

```
question
  → plan            route the question: sql | clarify | chit_chat
      ├─ clarify / chit_chat → answer directly, no database access
      └─ sql
          → generate      LLM writes a single SELECT (with recent history as context)
          → guardrail     safety + confidentiality checks + result-size estimate
              ├─ block   → refuse (destructive, or exposes restricted PII), never runs
              ├─ review  → PAUSE, hand the SQL to a human (approve / reject / modify)
              └─ allow   → execute
          → execute       run read-only against SQLite
              └─ error?  → repair: feed the error back to the LLM, re-check, retry
          → summarize     write a grounded natural-language answer from the result rows
          → verify        score the answer's faithfulness against those rows
```

There are two confidentiality checkpoints, not one. An **early planner gate** refuses a request for an individual's personal data before any SQL is generated (fast, cheap), and the **deterministic SQL guardrail** is the authoritative backstop that inspects the actual columns a query would touch. The early gate can be fooled by phrasing; the SQL gate cannot, because it reads the real query.

The **guardrail `review` branch is the human-in-the-loop checkpoint.** A flagged query is stored as `needs_approval` and returned *without touching the database*; a later `POST /api/review` resumes it. Crucially, a reviewer who edits a flagged query into something destructive or PII-exposing is **re-blocked by the guardrail** — the human gate cannot override the hard rules.

### Guardrail rules

| Rule | Decision | Trigger |
| --- | --- | --- |
| `non_select` / `destructive_keyword` | block | anything that isn't a read-only SELECT (DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE…) |
| `multiple_statements` | block | more than one statement (blocks `; DROP …` injection) |
| `restricted_pii` | block | query exposes a restricted `workers` column at the **row level** (national_id, home_address, phone, medical_conditions, monthly_salary_aed), or a blanket `SELECT *` that would sweep them |
| `join_complexity` | review | more JOINs than `MAX_JOINS` (default 2) |
| `large_result` | review | estimated rows over `MAX_RESULT_ROWS` (default 200) with no LIMIT |

The three block rules cover three distinct risks: **safety** (don't mutate data), **confidentiality** (don't leak personal data), and injection (one statement only). The confidentiality rule is deterministic — it does not rely on the model choosing to refuse.

**Aggregate vs. row-level PII.** The confidentiality rule is column-aware: a *statistic computed over* a sensitive column is allowed, while any *row-level* exposure of it is blocked. So `SELECT AVG(monthly_salary_aed) ... GROUP BY site_id` runs (no individual's salary is revealed), but `SELECT monthly_salary_aed FROM workers`, a `WHERE monthly_salary_aed > 30000` filter, or grouping by the raw column are all blocked. Filtering or grouping on raw sensitive values is treated conservatively because rare-category counts can re-identify people in small groups.

Every decision and every human choice is written to the SQLite memory log, so the conversation history doubles as an audit trail.

Runs with zero API keys: a keyword-matched mock provider exercises the full pipeline offline, so the app, dataset, and memory log are fully demonstrable without a key.

## Dataset

Synthetic, generated with Faker (`backend/scripts/generate_data.py`), persisted to SQLite at `backend/data/operations.db`. ~7,000 rows across four tables, covering 6 sites and 120 workers over a 180-day window.

| Table | Rows | Grain |
| --- | --- | --- |
| `safety_incidents` | ~750 | one row per reported incident |
| `worker_vitals` | ~5,100 | one row per sensor reading |
| `operational_metrics` | 1,080 | one row per site per day |
| `workers` | 120 | one row per worker — personnel directory, contains restricted PII |

Distributions are hand-tuned, not uniform: severity is skewed toward `low` (~55%) with `critical` rare (~3%); incident resolution status depends on how old the incident is (recent incidents skew `open`/`in_progress`, older ones skew `resolved`/`closed`); heart rate and body temperature are correlated with `activity_level`, with occasional heat-stress outliers; and `productivity_index` is negatively correlated with `incidents_reported` and `near_misses` for the same site-day.

The `workers` table deliberately mixes routine fields (`full_name`, `role`, `hire_date`) with **restricted personal data** (`national_id`, `home_address`, `phone`, `medical_conditions`, `monthly_salary_aed`). These sensitive columns exist specifically so the confidentiality guardrail has something to protect — asking for worker salaries or medical conditions is refused, while asking for role counts or hire dates runs normally.

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

That runs with `LLM_PROVIDER=mock`, so SQL comes from keyword rules rather than a real model, but the full orchestration — routing, SQL generation, guardrails, human review, execution, repair, memory logging — is live. Open http://localhost:8000 for the query UI (question box, guardrail badges, step trace, and an approve/reject/modify panel for flagged queries).

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
python scripts/smoke_test.py     # offline; exercises every branch: routing, guardrail block,
                                 # human review (approve/reject/modify), summarization, repair
python scripts/run_eval.py       # scored guardrail + routing regression suite (exits non-zero on fail)
```

`run_eval.py` reads `data/eval_set.json` — a set of SQL strings tagged with their expected guardrail decision, plus questions tagged with their expected route — and reports a pass rate. Any prompt or rule change that quietly breaks a guardrail shows up as a failed case. It runs on the mock provider, so it is deterministic and needs no key.

## Project structure

```
backend/
  app/
    config.py     settings from .env (guardrail thresholds, repair budget)
    db.py         SQLite connection, schema description, row-estimate + query runner
    llm.py        Anthropic / OpenAI / mock provider abstraction (plan, generate, repair)
    guardrails.py named safety + confidentiality rules -> allow | review | block, with reasons
    trace.py      per-step timing spans
    agent.py      the orchestrator: plan -> guardrail -> review -> execute -> repair -> summarize -> verify
    memory.py     conversation memory + decision log (SQLite); pending-review state machine; stats
    evals.py      guardrail + routing regression suite
    schemas.py    pydantic request/response models
    main.py       FastAPI app and endpoints
    static/       the query UI (Ask + Decision-log dashboard)
  data/
    operations.db      generated synthetic dataset (gitignored, regenerate with the script below)
    eval_set.json      tagged regression cases
  scripts/
    generate_data.py   synthetic dataset generator (Faker)
    smoke_test.py      offline end-to-end check across all branches
    run_eval.py        scored regression suite
```

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/query` | orchestrate a question; returns route, SQL, guardrail decision, grounded answer + faithfulness, results (or a `needs_approval` turn), and the full trace |
| `POST /api/review` | resume a flagged turn: `{turn_id, decision: approve\|reject\|modify, modified_sql?, reason?}` |
| `GET /api/history` | recent turns from the memory log / decision trail |
| `GET /api/stats` | aggregate decision-log metrics (by status / decision / route, approval rate) |
| `POST /api/eval` | run the guardrail + routing regression suite, return the scored report |
| `GET /api/health` | active LLM provider and current guardrail thresholds |


## What this demonstrates

Multi-step agent orchestration, a SQL safety and confidentiality layer, human-in-the-loop control flow, execution tracing, provider abstraction, conversation memory as an audit trail, and synthetic dataset generation with realistic distributions — built without a framework doing the thinking.

Built by Pavan Adithya Chaganti · [LinkedIn](https://www.linkedin.com/in/pavan-adithya-chaganti-763840214)
