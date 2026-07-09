# Deploying with Coolify

The whole app is a single container: the FastAPI backend serves the API **and**
the UI, and the synthetic dataset is baked into the image at build time. So a
deploy is one service pointed at this repo's `Dockerfile` — no separate frontend
host, no database to provision.

## Prerequisites

1. A server running [Coolify](https://coolify.io) (self-hosted on any VPS via its
   install script), or a Coolify Cloud instance.
2. This GitHub repo connected to Coolify — either as a **Public Repository**
   (paste the URL) or a **Private Repository** via the Coolify GitHub App.
3. Optional, for real answers: an `ANTHROPIC_API_KEY`
   (https://console.anthropic.com) or `OPENAI_API_KEY`. With no key the app runs
   in `mock` mode — the full pipeline works, SQL just comes from keyword rules.

## Steps

1. **New resource.** In your Coolify project/environment: **+ New** →
   **Public Repository** (or Private via the GitHub App) → paste
   `https://github.com/pavanadithyachaganti/self-governing-sql-agent`.
2. **Build pack: Dockerfile.** Coolify auto-detects the `Dockerfile` at the repo
   root. Leave the base directory as `/`.
3. **Port.** Set the exposed port to **8000** (matches `EXPOSE 8000`).
4. **Environment variables** (Settings → Environment Variables):

   | Key | Value | Notes |
   | --- | --- | --- |
   | `LLM_PROVIDER` | `anthropic` | or `openai`, or leave `mock` for a keyless demo |
   | `ANTHROPIC_API_KEY` | `sk-ant-…` | mark as a secret; only needed for real answers |
   | `ANTHROPIC_MODEL` | `claude-sonnet-5` | optional override |
   | `MAX_RESULT_ROWS` | `200` | optional guardrail tuning |
   | `AGENT_MODE` | `single` | set `multi` to default to the specialist council |

5. **Health check (optional).** Point Coolify's health check at `GET /api/health`.
6. **Deploy.** Coolify builds the image (which runs `generate_data.py`), starts
   the container, and — via its built-in proxy — gives you an HTTPS URL with a
   Let's Encrypt certificate. Add your own domain under **Domains** if you like.

Open the URL: the Ask view is at `/`, and the OpenAPI docs at `/docs`.

## Persistence (optional)

The conversation memory / decision log is written to `/app/.data/memory.db`
inside the container, so it resets on each redeploy. To keep the audit trail
across deploys, add a **Persistent Storage** volume in Coolify mounted at
`/app/.data`. The dataset itself (`/app/data/operations.db`) is baked into the
image and needs no volume.

## Resources

No torch, no local models — the image is small and runs comfortably on a
256–512 MB instance. Each query makes a few short LLM API calls when a real
provider is configured.

## Redeploying

Coolify can auto-deploy on push (enable the webhook), or hit **Redeploy** in the
dashboard. Because the dataset is regenerated during the build, a redeploy also
refreshes it (with the fixed seed, so it's identical).
