FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY backend/ .

# Bake the synthetic dataset into the image so the container is self-contained
# and needs no writable data volume to answer queries.
RUN python scripts/generate_data.py

# The conversation memory / decision log is written here at runtime. Make it
# world-writable so it works whether the platform runs the container as root or
# as a non-root user (e.g. Hugging Face Spaces).
RUN mkdir -p /app/.data && chmod 777 /app/.data

# Runs with LLM_PROVIDER=mock out of the box (no key needed). Set
# LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY (or the OpenAI equivalents) for
# real SQL generation. PORT is honored if the platform injects one.
ENV LLM_PROVIDER=mock
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
