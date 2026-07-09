# Deploying free on Render

The whole app is a single container: the FastAPI backend serves the API **and**
the UI, and the synthetic dataset is baked into the image at build time. Render
builds the repo's `Dockerfile` and runs it as one **free web service** — no
credit card, HTTPS included.

## Steps

1. **Sign in** at https://render.com with your GitHub account (free; no card).

2. **Create the service** — two ways:
   - **Blueprint (one click):** New → **Blueprint** → pick this repo. Render reads
     [`render.yaml`](render.yaml) and creates the service preconfigured.
   - **Manual:** New → **Web Service** → this repo → **Language: Docker**. Render
     auto-detects the `Dockerfile`. Instance type **Free**.

3. **Environment variables** (Settings → Environment). Optional — it runs keyless
   in `mock` mode by default:

   | Key | Value | Notes |
   | --- | --- | --- |
   | `LLM_PROVIDER` | `anthropic` | switch from `mock` for real answers |
   | `ANTHROPIC_API_KEY` | `sk-ant-…` | add the value here, never in git |
   | `AGENT_MODE` | `multi` | optional: default to the specialist council |

   If `LLM_PROVIDER=anthropic` but no key is set, the app safely falls back to
   `mock`, so a keyless deploy still works.

4. **Deploy.** Render builds the image (which runs `generate_data.py`), starts
   the container on its injected `$PORT` (the `Dockerfile` honors it), and gives
   you an HTTPS URL like `https://self-governing-sql-agent.onrender.com`. The Ask
   view is at `/`, the API docs at `/docs`. The health check hits `/api/health`.

## Notes

- **Free instances sleep** after ~15 minutes of inactivity and cold-start
  (~50 s) on the next request. Fine for a demo; upgrade the instance for
  always-on.
- **Secrets stay secret** — the API key lives in Render's environment, never in
  the repo. `.env` and the databases are gitignored.
- **Memory / decision log** is written to `/app/.data` inside the container and
  resets on redeploy — fine for a demo.
- **Auto-deploy** on every push to `main` is on by default.

## Same image, other hosts

The `Dockerfile` is platform-agnostic and honors `$PORT`, so the same image also
deploys as one service on **Koyeb** (free, always-on) or **Fly.io** (needs a
card), and on a **Hugging Face Docker Space** where that's available on your
account.
