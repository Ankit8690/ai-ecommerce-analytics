# AI-Enhanced E-Commerce Data Analytics & Business Intelligence Platform

An end-to-end **data analytics + BI** platform over a public e-commerce marketplace
dataset. The trusted analytics layer is PostgreSQL 18 with a curated `analytics.*`
schema of business-defined views. On top of that:

- **Streamlit BI dashboards** (executive overview, sales, products & categories,
  customers & segments, customer experience, delivery & operations, forecasting)
- **FastAPI backend** exposing the analytics views as REST endpoints
- **AI enhancement layer** — natural-language → SQL (Gemini), an RAG knowledge
  assistant grounded in project documentation, and an evidence-first decision-support
  engine — all constrained to the same safe read-only path
- **Power BI / Tableau integration ready** — see [docs/BI_INTEGRATION.md](docs/BI_INTEGRATION.md)
  for connection settings, view catalog, and recommended dashboard layouts
- **Cloud deployment** — see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the
  Render.com blueprint (managed PostgreSQL + two Docker web services on HTTPS)

The AI is an *enhancement* over the analytics foundation, not a replacement for it.

> **Status:** Phase 12 complete — Dockerized 3-service stack, 230/230 pytest cases green.
> See [PROJECT_STATUS.md](PROJECT_STATUS.md).

---

## How to run the application

### Option A — Docker Compose (recommended, Phase 12)

Prerequisites: **Docker Desktop 4.86+** (WSL 2 backend on Windows), and a populated `.env` (copy from `.env.example`, fill in `APP_DB_PASSWORD`, `READONLY_DB_PASSWORD`, `POSTGRES_ADMIN_PASSWORD`, and — optionally — `LLM_API_KEY`).

```bash
# 1. Build the application image (installs deps + bakes the RAG index)
docker compose build

# 2. First-time only: start Postgres, then load schema + 8 CSVs + analytics views
docker compose up -d postgres
docker compose --profile init up db-init --exit-code-from db-init

# 3. Start the stack
docker compose up -d
```

* **Dashboard**: http://localhost:8501
* **API (Swagger UI)**: http://localhost:8000/docs
* **Health**: http://localhost:8000/health

Everyday commands:

```bash
docker compose ps                  # service status + health
docker compose logs -f api         # follow api logs
docker compose logs -f dashboard   # follow dashboard logs
docker compose stop                # stop, keep data
docker compose down                # stop and remove containers, keep Postgres volume
docker compose down -v             # ALSO deletes the Postgres data volume (re-init required)
```

Postgres data is persisted in the named volume `ecommerce_postgres_data`, so `docker compose down` and `docker compose up` preserve the loaded dataset. Only `docker compose down -v` wipes it — after which you re-run step 2.

To rebuild the RAG index (after editing docs) rebuild the image:

```bash
docker compose build --no-cache api && docker compose up -d
```

Postgres is **not** exposed to the host by default — services reach it via the Docker network as `postgres:5432`. Uncomment the `ports:` block in `docker-compose.yml` if you need `psql` from the host.

### Option B — Local (no Docker)

Assumes host PostgreSQL 18 is running and `.env` points at it.

```powershell
# Terminal 1 — FastAPI
.venv\Scripts\python.exe -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
# Terminal 2 — Streamlit
.venv\Scripts\python.exe -m streamlit run dashboard.py
```
* **API Documentation (Swagger)**: `http://127.0.0.1:8000/docs`
* **Health Check**: `http://127.0.0.1:8000/health`
* **Dashboard URL**: `http://localhost:8501`

---

## About the dataset

99,441 orders placed on an online marketplace between **September 2016 and
October 2018**, across 8 relational tables — customers, orders, order items,
payments, reviews, products, sellers and geolocation.

**A note on provenance.** This is a public **Brazilian** e-commerce dataset
(Olist) that has been redistributed with Indian city and state names substituted
for the originals. The geolocation coordinates are still Brazilian, and the
`UPI` payment type sits where the Brazilian `boleto` was. This project therefore
treats the data as *a public e-commerce dataset with relabelled geography* and
makes no claim about the country of origin. Monetary values are reported in
**unspecified currency units**, not rupees.

Being straight about this is deliberate: the analysis is real, so the claims
about it should be too.

---

## Planned architecture

```
Dataset/raw/*.csv
        │  one-time load
        ▼
┌────────────────────┐
│   PostgreSQL 18    │  source of truth · 8 tables · PK/FK constraints
└─────────┬──────────┘
          │  app user (read/write)          read-only user
          │                                        │
          ▼                                        │
┌────────────────────┐                             │
│  SQL analytics     │  revenue, cohorts, RFM, delivery SLAs
└─────────┬──────────┘                             │
          ▼                                        │
┌────────────────────┐                             │
│  Python / ML       │  segmentation · experience risk · forecasting
└─────────┬──────────┘                             │
          ▼                                        ▼
┌─────────────────────────────────────────────────────┐
│                  FastAPI backend                    │
│  REST endpoints · Pydantic validation               │
│  LLM analyst → SQL validator → read-only connection │
│  RAG retriever over project documents               │
└─────────────────────┬───────────────────────────────┘
                      │ HTTP
                      ▼
┌────────────────────┐
│ Streamlit dashboard│
└────────────────────┘
```

The dashboard never queries the database directly — it goes through the API.
The LLM never reaches the database except through a validator *and* a read-only
role. See [DECISIONS.md](DECISIONS.md) D-005 and D-006.

---

## Planned features

| Feature | Supported by the data? |
|---|---|
| Revenue, order and category analytics | ✅ Yes |
| Delivery performance & SLA analysis | ✅ Yes — 8.11% of orders arrive late |
| RFM customer segmentation | ✅ Yes |
| Customer experience risk (negative-review prediction) | ✅ Yes — 15.1% negative reviews |
| Monthly sales forecasting | ✅ Yes — 20 clean months |
| Seller performance scorecards | ✅ Yes — 3,095 sellers |
| LLM business analyst (natural language → SQL) | ✅ Yes |
| RAG knowledge assistant | ✅ Yes (over project docs) |
| **Customer churn prediction** | ❌ **No** — only 3.12% of customers ever reorder |

Churn was cut on evidence, not on effort. With a 3.12% repeat rate any churn
label is ~97% one class, and the resulting model would be a decoration. The
project models experience risk instead, which the data genuinely supports.
Reasoning in [DECISIONS.md](DECISIONS.md) D-007.

---

## Tech stack

**Database** PostgreSQL 18 · SQLAlchemy
**Backend** FastAPI · Pydantic
**Frontend** Streamlit
**Analytics/ML** Pandas · NumPy · scikit-learn · XGBoost
**Packaging** Docker

Intentionally *not* used: React, Node.js, Redis, Kubernetes, microservices.
The architecture is meant to be simple enough to explain in an interview and
honest enough to survive follow-up questions.

---

## Getting started

Setup instructions land in Phase 1, once there is something to run.
Requirements today: Python 3.11, PostgreSQL 18, Git.

---

## Repository layout

```
AI_E-Commerce_Analytics/
├── CLAUDE.md              # working agreement for AI-assisted development
├── DECISIONS.md           # architecture decision record
├── PROJECT_STATUS.md      # phase board and verification checklists
├── README.md
└── Dataset/
    ├── archive (4).zip    # original download
    └── raw/               # 8 extracted CSVs
```

Further directories are added as phases complete.

---

## Documentation

- [PROJECT_STATUS.md](PROJECT_STATUS.md) — what is done, what is next, how each phase is verified
- [DECISIONS.md](DECISIONS.md) — architecture decisions and the reasoning behind them
- [CLAUDE.md](CLAUDE.md) — engineering rules for this repository
