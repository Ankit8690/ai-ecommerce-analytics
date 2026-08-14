# AI-Powered E-Commerce Business Intelligence & Decision Support Platform

An end-to-end analytics platform over a public e-commerce marketplace dataset:
a PostgreSQL warehouse, a SQL analytics layer, machine-learning models, a FastAPI
backend, a Streamlit dashboard, and an LLM business analyst that answers questions
in natural language through a safety-constrained SQL path.

> **Status:** Phase 5 complete (Executive BI Dashboard & API active).
> See [PROJECT_STATUS.md](PROJECT_STATUS.md).

---

## How to run the application

### 1. Start the FastAPI backend API
```powershell
.venv\Scripts\python.exe -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```
* **API Documentation (Swagger)**: `http://127.0.0.1:8000/docs`
* **Health Check**: `http://127.0.0.1:8000/health`

### 2. Start the Streamlit BI Dashboard
In a second terminal:
```powershell
.venv\Scripts\python.exe -m streamlit run dashboard.py
```
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
