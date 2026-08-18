# AI-Enhanced E-Commerce Data Analytics & Business Intelligence Platform

An end-to-end **data analytics + BI** platform built over a public e-commerce marketplace
dataset. PostgreSQL 18 is the source of truth; a curated `analytics.*` schema defines every
business metric exactly once; FastAPI exposes that schema as a REST service; a Streamlit
dashboard consumes it; and three AI layers (natural-language → SQL, RAG knowledge assistant,
evidence-first recommendation engine) sit on top — each one constrained to the same
read-only, validator-gated database path.

> **Status:** Phase 13 complete — Dockerized 3-service stack deployed to Render, **230 / 230
> pytest cases pass**, all three Gemini paths verified live end-to-end.
> See [PROJECT_STATUS.md](PROJECT_STATUS.md).

The AI is an **enhancement** over the analytics foundation, not a replacement for it. Every
number a user sees traces back to a specific `analytics.*` view. The LLM only rewrites
narratives — it never introduces numbers of its own.

---

## 🌐 Live demo

- **Dashboard**: https://ecommerce-dashboard-q4bh.onrender.com/
- **API docs**: https://ecommerce-api-q4bh.onrender.com/docs
- **Health**: https://ecommerce-api-q4bh.onrender.com/health

> First request after ~15 min idle takes 30-45 s to wake up (Render free tier). Hit
> `/health` a minute before demoing.

---

## Architecture

```
              Olist e-commerce dataset (8 CSVs · 99,441 orders)
                                │  one-time load
                                ▼
                    ┌────────────────────────┐
                    │      PostgreSQL 18     │  source of truth
                    │  public.* raw tables   │  ─────────────────
                    │  analytics.* views     │  business logic once
                    └────────────┬───────────┘
                                 │  ecommerce_readonly role (SELECT only)
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
       ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐
       │  Streamlit  │   │   FastAPI   │   │  Power BI /      │
       │  BI pages   │   │   /api/*    │   │  Tableau ready   │
       └──────┬──────┘   └──────┬──────┘   │  (see docs)      │
              │                 │           └──────────────────┘
              └────────┬────────┘
                       ▼
                 AI Enhancement Layer
     ┌───────────────┬───────────┬───────────────────┐
     ▼               ▼           ▼                   ▼
   NL → SQL         RAG      Recommendations    Deterministic
   (Gemini)     (TF-IDF)     (evidence-first)   fallbacks for
                                                everything
```

The dashboard **never queries the database directly** — it goes through the API. The LLM
**never reaches the database** except through a validator *and* a read-only role. See
[DECISIONS.md](DECISIONS.md) D-005 and D-006.

---

## Phase-by-phase build

Each phase was independently verified before the next started — see
[PROJECT_STATUS.md](PROJECT_STATUS.md) for per-phase checklists.

### Phase 0 — Discovery
Established the working agreement (`CLAUDE.md` / `AGENTS.md`), locked the project root
outside OneDrive to avoid sync conflicts on `.venv` and DB dumps.

### Phase 1 — Dataset audit + PostgreSQL warehouse
Wrote a reusable audit (`scripts/audit_dataset.py`): row counts, dtypes, PK/FK integrity,
percentiles, reconciliation checks. Loaded all 8 CSVs into a fresh PostgreSQL 18 database
under a dedicated `ecommerce_app` role. Documented findings in `docs/data_dictionary.md`,
`docs/data_quality_report.md`, `docs/database_relationships.md`. Established the
`ecommerce_readonly` role for read-only application access.

**Key finding**: dataset is Olist (Brazilian) relabelled with Indian names — documented
transparently in `DECISIONS.md` D-002. Currency reported in unspecified units, not INR.

### Phase 2 — SQL analytics layer
Built 10 curated views in the `analytics.*` schema. Business metrics defined *once*:
GMV = `SUM(price + freight_value)`, negative review = `review_score ≤ 2`, on-time = actual
delivery date ≤ estimated date. Every downstream layer (API, dashboard, BI tools, AI) reads
from these views instead of re-deriving.

### Phase 3 — ML & advanced analytics
- **Customer segmentation** via K-Means over RFM features → 4 segments (Champions, Loyalists,
  Recent Buyers, Lapsed), materialised into `analytics.customer_segments`.
- **Sales forecasting** on 20 clean months → 3-month forward GMV forecast with 95% CI.
- **Experience-risk model** predicting `review_score ≤ 2` from delivery + freight features.
- Churn deliberately **not** modelled — only 3.12% of customers ever reorder; documented in
  `DECISIONS.md` D-007.

### Phase 4 — FastAPI backend
REST endpoints over the analytics views (`/api/kpis`, `/api/sales/monthly`, `/api/products`,
`/api/reviews`, `/api/delivery`, `/api/customers/{id}`, `/api/forecast`, `/api/analyst`).
Pydantic validation on every request/response. Swagger UI auto-published at `/docs`.

### Phase 5 — Streamlit BI dashboard
Seven analytics pages consuming the FastAPI endpoints: Executive Overview, Sales & Revenue,
Products & Categories, Customers & Segments, Customer Experience, Delivery & Operations,
Sales Forecasting. Plotly for interactive charts.

### Phase 6 — LLM analyst + safe NL → SQL
Natural-language question → SQL → validator → read-only PostgreSQL → grounded answer. Two
layers of safety:
1. **SQL validator** (`ai/sql_validator.py`): blocks DML/DDL, catalog probes, multi-statement,
   comments; enforces `analytics.` / `public.` schema allowlist; auto-appends `LIMIT 100`.
2. **Read-only role** at the database level — cannot mutate data even if the validator were
   bypassed.

### Phase 6.1 — NL → SQL hardening + direct SQL editor + question library
- Priority inversion: **deterministic local parser tried first**, Gemini as fallback for
  novel phrasings. Local parser is provably correct on ranking / metric / entity questions.
- Direct SQL editor (💻 SQL Query page) — same validator + read-only role, safe to expose.
- Question library of **64 curated questions**, every one verified end-to-end (audit script
  at `scripts/audit_question_library.py`).

### Phase 7 — RAG knowledge assistant
TF-IDF retrieval over project documentation (`docs/*.md` + `DECISIONS.md`) — 51 chunks with
heading-aware chunking and MD5 dedup. Answers definitional / business-rule questions ("what
is GMV?", "why isn't churn modelled?") that SQL can't. Cites every source. Deterministic
citation-only fallback when Gemini is unavailable.

### Phase 8 — AI business recommendations
Evidence-first decision-support engine. 7 recommendation categories (category quality,
product risk, delivery SLA, review health, sales trend, customer segments, KPI health).
Every recommendation returns a structured `RecommendationPackage` where each number traces
back to a specific `analytics.*` view. Gemini only *rewrites* the narrative — never
introduces numbers.

### Phase 11 — Testing, security & production readiness
- 230 pytest cases across 6 modules (SQL editor accuracy, RAG pipeline, decision support,
  SQL validator unit, RAG chunker unit, API smoke, security & failure handling).
- API error responses hardened to never leak DB credentials or stack traces.
- Input length capped (413 on > 1000 chars).
- Prompt-injection resistance tested (retrieved doc text is data, not instructions).
- Read-only role enforcement verified at the DB layer.

### Phase 12 — Docker deployment
Reproducible 3-service stack: `postgres` (persistent volume) + `api` (FastAPI, healthchecked)
+ `dashboard` (Streamlit). One-shot `db-init` compose profile for the initial data load.
RAG index baked into the image at build time.

### Phase 13 — Cloud deployment
Rendered on Render.com via `render.yaml` blueprint: managed PostgreSQL + two Docker web
services on HTTPS. Cold-start-tolerant timeouts, auto-retry on transient 5xx, graceful
degradation when ML tables are absent.

---

## 5-step interview demo (5 minutes flat)

Practice this path — it hits every architectural highlight in the right order.

### Step 1 — Executive Overview (30 s)
Sidebar → **Executive Overview**. Point at the KPI card:
> "99,441 orders, GMV 15.8M units, cash collected 16.0M. These numbers come from
> `analytics.v_executive_kpis` — a single curated view. GMV and cash differ because of
> installment payments — I documented that as data-quality issue DQ-15."

### Step 2 — SQL Query editor (60 s)
Sidebar → **💻 SQL Query**. Paste:
```sql
SELECT * FROM analytics.v_executive_kpis
```
Point out the same numbers appear.
> "Same data, direct SQL. The validator lets any SELECT through, but…"

Paste each of these and show the block:
```sql
DROP TABLE public.orders
```
→ *"Query must start with SELECT or WITH (got DROP)"*
```sql
SELECT * FROM public.unicorns
```
→ *"Unknown table/view"*
```sql
SELECT * FROM information_schema.tables
```
→ *"Reference to information_schema is prohibited"*

> "Even if the validator missed something, the `ecommerce_readonly` role at the database
> layer can't mutate anything. Defence in depth."

### Step 3 — AI Analyst (60 s)
Sidebar → **🤖 AI Business Analyst**. Type or click a library question:
> **"Top 3 least reviewed products"**

Answer returns in ~5 s. Point at the source line:
> "Source: `analytics.v_product_performance (local parser)`. My deterministic parser
> recognised the pattern *top N X by metric*, generated the SQL with the right ORDER BY,
> validated it, and executed on the same read-only role. No LLM in the loop — Gemini is
> only used for phrasings the parser can't understand."

### Step 4 — Knowledge Base / RAG (45 s)
Sidebar → **📚 Knowledge Base**. Ask:
> **"What is Product GMV and how is it defined?"**

Show retrieved excerpts with citations to `docs/data_dictionary.md` and
`docs/data_quality_report.md`.
> "RAG for definitions — SQL can't answer 'what does this metric mean?'. The retriever
> is TF-IDF over 51 chunks of project docs; every answer cites its sources verbatim
> so nothing is fabricated."

### Step 5 — Recommendations (60 s)
Sidebar → **💡 Recommendations**. Click:
> **"How is our shipping SLA performing?"**

Point at the structured Evidence block:
> "Every number here — 96,470 delivered orders, 12.56 avg days, 8.11% late-delivery rate —
> comes from `analytics.v_delivery_performance`. The recommendation itself is grounded in
> those numbers. When Gemini's available it rewrites the narrative; when it's not, the
> deterministic package is what you see. The LLM is a rewriter, not a source of truth."

### Closing line (15 s)
> "The thing I'm proudest of isn't any single feature. It's that the AI layer *cannot*
> misrepresent the data — the validator, the read-only role, the evidence-first package,
> the deterministic fallbacks — they're all belt-and-braces. That's what makes this
> interview-defensible rather than a demo that only works when the model behaves."

### What to say if something goes wrong
| Symptom | Line |
|---|---|
| Gemini takes 60 s | "Free-tier Gemini throttles requests. Paid tier responds in 3-5 s. The deterministic fallback still returns the right answer either way." |
| Render cold-start 502 | "Free-tier services sleep after 15 min idle. Production would use Starter tier ($7/mo) for always-on. Let me switch to the local Docker instance." |
| 500 on customer segment lookup | "The ML segmentation table is populated per-environment via a one-shot script. Rest of the app is unaffected." |

---

## How to run the application

### Option A — Docker Compose (recommended)

Prerequisites: **Docker Desktop 4.86+** (WSL 2 backend on Windows). Copy `.env.example` to
`.env` and fill in `APP_DB_PASSWORD`, `READONLY_DB_PASSWORD`, `POSTGRES_ADMIN_PASSWORD`,
and optionally `LLM_API_KEY`.

```bash
# 1. Build the application image (installs deps + bakes the RAG index)
docker compose build

# 2. First-time only: start Postgres, then load schema + 8 CSVs + analytics views
docker compose up -d postgres
docker compose --profile init up db-init --exit-code-from db-init

# 3. Start the stack
docker compose up -d
```

- **Dashboard**: http://localhost:8501
- **API (Swagger UI)**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health

Everyday commands:

```bash
docker compose ps                  # service status + health
docker compose logs -f api         # follow api logs
docker compose logs -f dashboard   # follow dashboard logs
docker compose stop                # stop, keep data
docker compose down                # stop + remove containers, keep Postgres volume
docker compose down -v             # ALSO deletes the Postgres volume (re-init required)
```

Postgres data persists in the named volume `ecommerce_postgres_data`. Only
`docker compose down -v` wipes it. Rebuild the RAG index (after editing docs) by rebuilding
the image:

```bash
docker compose build --no-cache api && docker compose up -d
```

### Option B — Local (no Docker)

Assumes host PostgreSQL 18 is running and `.env` points at it.

```powershell
# Terminal 1 — FastAPI
.venv\Scripts\python.exe -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
# Terminal 2 — Streamlit
.venv\Scripts\python.exe -m streamlit run dashboard.py
```

- API docs: `http://127.0.0.1:8000/docs`
- Dashboard: `http://localhost:8501`

### Option C — Cloud (Render)

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full walkthrough: push to GitHub →
Blueprint → set 2 secrets → seed DB from your laptop → wire CORS → smoke test.

---

## About the dataset

99,441 orders placed on an online marketplace between **September 2016 and October 2018**,
across 8 relational tables: customers, orders, order items, payments, reviews, products,
sellers, geolocation.

**A note on provenance.** This is a public **Brazilian** e-commerce dataset (Olist)
redistributed with Indian city and state names substituted for the originals. The
geolocation coordinates are still Brazilian, and the `UPI` payment type sits where the
Brazilian `boleto` was. This project therefore treats the data as *a public e-commerce
dataset with relabelled geography* and makes no claim about the country of origin.
Monetary values are reported in **unspecified currency units**, not rupees.

Being straight about this is deliberate: the analysis is real, so the claims should be
too.

---

## What's supported vs deliberately excluded

| Feature | Supported? | Reasoning |
|---|---|---|
| Revenue / GMV / AOV analytics | ✅ | Full order-item history |
| Delivery performance & SLA | ✅ | 8.11% of orders arrive late — real signal |
| RFM customer segmentation | ✅ | K-Means on 96,096 unique customers |
| Customer experience risk model | ✅ | 15.1% negative-review base rate — real class balance |
| Monthly sales forecasting | ✅ | 20 clean months of continuous history |
| Seller performance scorecards | ✅ | 3,095 sellers, all active |
| Product-category performance | ✅ | 71 categories, 32,951 products |
| Natural-language → SQL | ✅ | Small schema fits in a prompt; validator gates every query |
| RAG over project documentation | ✅ | Answers definitional questions SQL can't |
| **Customer churn prediction** | ❌ | Only 3.12% of customers ever reorder — a churn label collapses to ~97% one class. See D-007. |
| **Cohort retention curves** | ❌ | No repeat cohorts to track. |
| **Review-text NLP / sentiment** | ❌ | No text columns exist (DQ-14). |
| **Profit / margin analytics** | ❌ | No cost data. |
| **Geographic map visualisations** | ❌ | Brazilian coordinates under Indian labels (D-008). |

Every ❌ is documented with the evidence that produced the decision. This is what
"interview-defensible" means — I can defend both what's built and what's not.

---

## Tech stack

| Layer | Choice | Why (short) |
|---|---|---|
| Database | **PostgreSQL 18** | Real SQL (window functions, `DATE_TRUNC`, roles). SQLite lacks the permission model; MongoDB loses the joins. |
| DB driver | **SQLAlchemy 2.0** + **psycopg** | Standard Python stack. Connection pooling for free. |
| Analytics / ML | **Pandas · NumPy · scikit-learn** | Right-sized for 99k orders. Spark would be architecture I don't need. |
| Backend | **FastAPI · Pydantic · Uvicorn** | Auto Swagger docs, type-safe I/O, native async. Flask would need everything bolted on. |
| Frontend | **Streamlit · Plotly** | Python-native interactive UI in hours, not weeks. React would double dev time. |
| LLM | **Google Gemini** (via `google-genai`) | Free tier usable, clean SDK, provider hidden behind interface → swappable. |
| RAG | **sklearn TfidfVectorizer** | Zero new deps, deterministic, right-sized for 51 chunks. FAISS/Chroma would be overkill. |
| Container | **Docker + docker-compose** | Reproducible for reviewers. Kubernetes would be airlift for a wheelbarrow. |
| Deploy | **Render.com** | Real free tier including managed Postgres; reads Docker directly. |
| Test | **pytest + TestClient + monkeypatch** | 230 cases across 6 modules, including failure-mode tests. |

**Deliberately not used**: React, Node.js, Redis, Kubernetes, microservices, LangChain,
FAISS. Each is either overkill for the traffic or adds abstractions this pipeline
doesn't benefit from.

---

## Documentation

- [PROJECT_STATUS.md](PROJECT_STATUS.md) — phase board with verification checklists
- [DECISIONS.md](DECISIONS.md) — architecture decisions and reasoning
- [docs/data_dictionary.md](docs/data_dictionary.md) — every table + column
- [docs/data_quality_report.md](docs/data_quality_report.md) — 18 data-quality findings
- [docs/database_relationships.md](docs/database_relationships.md) — ERD + canonical joins
- [docs/BI_INTEGRATION.md](docs/BI_INTEGRATION.md) — Power BI / Tableau connection spec
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Render deployment walkthrough
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) — engineering rules for this repo

---

## Repository layout

```
AI_E-Commerce_Analytics/
├── ai/                       # Phase 6+ AI modules
│   ├── analyst_engine.py     # NL question → SQL → answer pipeline
│   ├── nl_to_sql.py          # Gemini NL→SQL with schema prompt
│   ├── nl_interpreter.py     # Deterministic local parser (preferred path)
│   ├── sql_validator.py      # Safety validator (Phase 6)
│   ├── decision_support.py   # Phase 8 recommendation engine
│   └── question_library.json # 64 curated demo questions
├── api/                      # Phase 4 FastAPI
│   ├── main.py               # app + CORS + health
│   ├── database.py           # readonly_engine
│   ├── schemas.py            # Pydantic models
│   └── routes/               # analyst · analytics · ml
├── database/
│   ├── schema.sql            # 8 public.* tables + FKs
│   ├── analytics_schema.sql  # 10 analytics.* views
│   └── seed.py               # bootstrap + CSV load
├── ml/                       # Phase 3 ML pipeline
├── rag/                      # Phase 7 RAG
│   ├── ingest.py             # markdown chunker + TF-IDF fit
│   ├── retriever.py          # cosine-similarity retriever
│   ├── synthesizer.py        # Gemini answer synthesis + fallback
│   └── index/                # pickled chunks + vectorizer
├── tests/                    # 230 pytest cases
│   ├── conftest.py
│   ├── test_sql_editor_wrapper.py
│   ├── test_rag_wrapper.py
│   ├── test_decision_support_wrapper.py
│   ├── test_sql_validator_unit.py
│   ├── test_rag_chunker_unit.py
│   ├── test_api_smoke.py
│   └── test_security_hardening.py
├── scripts/                  # ops + audit scripts
│   ├── audit_dataset.py
│   ├── audit_question_library.py
│   ├── build_rag_index.py
│   ├── apply_analytics_schema.py
│   ├── push_customer_segments.py
│   ├── run_all_tests.py
│   └── ...
├── docs/                     # data dictionary, DQ report, ERD, BI spec, deploy guide
├── docker/                   # postgres init script
├── Dockerfile
├── docker-compose.yml
├── render.yaml               # Render blueprint (Phase 13)
├── requirements.txt
├── dashboard.py              # Phase 5 Streamlit
├── .env.example
├── PROJECT_STATUS.md
├── DECISIONS.md
├── CLAUDE.md
├── AGENTS.md
└── README.md
```

---

## Regression test suite

Full suite: **230 tests, 100% pass, ~90 seconds**.

```bash
.venv\Scripts\python.exe -m pytest tests -q
# or
.venv\Scripts\python.exe scripts\run_all_tests.py
```

Per-file breakdown:

| Test file | Count | What it covers |
|---|---:|---|
| `test_sql_editor_wrapper.py` | 111 | End-to-end SQL editor accuracy (raw counts, view/raw reconciliation, ranking, aggregates, GROUP BY, joins, CTEs, window functions, filters, error paths) |
| `test_sql_validator_unit.py` | 28 | Validator rules (12 parametrized DML/DDL rejections, comments, multi-statement, catalog probes, unknown schemas, allowlist coverage) |
| `test_security_hardening.py` | 26 | Prompt-injection resistance, Gemini failure paths, DB failure paths, oversized-input, secret hygiene, recommendation robustness, read-only defence-in-depth |
| `test_decision_support_wrapper.py` | 25 | 7 recommendation categories, evidence integrity, hallucination guard, unsupported-question handling |
| `test_rag_wrapper.py` | 21 | Ingest, chunking, dedup, retrieval relevance, off-topic ceiling, min-score filter, synthesis fallback |
| `test_api_smoke.py` | 10 | Every FastAPI endpoint returns 200 with the expected shape |
| `test_rag_chunker_unit.py` | 7 | Markdown chunker edge cases (headings, preamble, long-section split, dedup) |
| **Total** | **230** | 100% pass |

---

## Credits

Dataset: [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/olistbr/brazilian-ecommerce), relabelled with Indian geography.
Built as a portfolio project to interview-defensible standards.
