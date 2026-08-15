# ─────────────────────────────────────────────────────────────────────
# Application image — used by both FastAPI (`api`) and Streamlit (`dashboard`).
# Same dependencies; the compose file picks the command per service.
# ─────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# System deps:
#   libpq5             — runtime for psycopg
#   postgresql-client  — psql CLI used by db-init compose profile
#   curl               — used by the api healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user
RUN groupadd -r app && useradd -r -g app -m -d /home/app app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps in a separate layer so code changes don't retrigger pip install.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source (see .dockerignore for what's excluded).
COPY . .

# Build the RAG TF-IDF index at image build time (no DB, no network).
RUN python scripts/build_rag_index.py

# Files created inside must be owned by the non-root runtime user.
RUN chown -R app:app /app
USER app

# api and dashboard both listen on 0.0.0.0. Ports are documented; compose maps them.
EXPOSE 8000 8501

# Default command is API; the dashboard service overrides `command` in compose.
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
