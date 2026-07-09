# Deploying on Hugging Face Spaces (free)

The whole app is a single container: the FastAPI backend serves the API **and**
the UI, and the synthetic dataset is baked into the image at build time. Hugging
Face Spaces builds the repo's `Dockerfile` directly and runs it — free, no credit
card, HTTPS included.

The Space config lives in the YAML block at the top of `README.md`
(`sdk: docker`, `app_port: 8000`). Hugging Face requires that block; it's
harmless on GitHub.

## Steps

1. **Create a free Hugging Face account** at https://huggingface.co and a
   **write access token** (Settings → Access Tokens → New token, role *write*).

2. **Create a Space:** https://huggingface.co/new-space
   - Owner: your account
   - Space name: e.g. `self-governing-sql-agent`
   - License: MIT
   - **Space SDK: Docker** → **Blank** template
   - Hardware: **CPU basic** (free)

3. **Push this repo to the Space.** A Space is its own git repo. From a clone of
   this project:

   ```bash
   git remote add space https://huggingface.co/spaces/<your-username>/self-governing-sql-agent
   git push space main
   ```

   When prompted, use your Hugging Face username and the **write token** as the
   password. Hugging Face reads the config from `README.md`, builds the
   `Dockerfile`, and starts the container.

   (Already have this repo cloned from GitHub? Just add the `space` remote
   alongside `origin` and push.)

4. **Add your API key as a secret** (for real answers; skip for a keyless `mock`
   demo). In the Space: **Settings → Variables and secrets**:

   | Type | Name | Value |
   | --- | --- | --- |
   | Secret | `ANTHROPIC_API_KEY` | `sk-ant-…` |
   | Variable | `LLM_PROVIDER` | `anthropic` |

   Optional variables: `ANTHROPIC_MODEL`, `MAX_RESULT_ROWS`, `AGENT_MODE=multi`.
   The Space restarts and picks them up.

5. **Open it.** Your app is live at
   `https://<your-username>-self-governing-sql-agent.hf.space` — the Ask view at
   `/`, the API docs at `/docs`.

## Notes

- **Free CPU basic** gives 2 vCPU / 16 GB RAM — plenty (no torch, no local
  models). The Space sleeps after ~48 h of inactivity and wakes on the next
  visit.
- **Secrets stay secret.** The API key lives in Space secrets, never in the
  repo. `.env` and the databases are gitignored.
- **Memory / decision log** is written to `/app/.data` inside the container
  (world-writable in the Dockerfile so it works under Hugging Face's non-root
  runtime). It resets when the Space rebuilds — fine for a demo.
- **Redeploys** happen automatically on every push to the Space's `main`.

## Same image, other hosts

The `Dockerfile` is platform-agnostic and honors `$PORT`, so the same image also
deploys as one service on Render, Koyeb, or Fly.io if you ever want an
always-on host.
