# CLAUDE.md — Working Agreement

Guidance for Claude Code when working in this repository.

## Project

AI-Powered E-Commerce Business Intelligence & Decision Support Platform.
Student/portfolio project, built to interview-defensible standards.

**Developer context:** beginner/intermediate. Comfortable with Python, Pandas,
NumPy, basic SQL, data visualization. Not yet comfortable with backend/database
integration. Explanations must assume competence in analysis, not in backend.

## Project root

`E:\AI_E-Commerce_Analytics` — the project root. Chosen over the OneDrive
Desktop folder to avoid sync locks on `.venv`, database dumps and caches.
Raw dataset: `Dataset/raw/*.csv` (extracted from `Dataset/archive (4).zip`).

## Non-negotiable rules

### Process
1. Never build the whole project in one step. Work phase by phase.
2. Inspect actual project state before starting a phase. Do not work from memory.
3. Verify before asserting. Never assume a file, table, column, package, API or
   env var exists — check it.
4. Every major feature ships with a test or an explicit verification step.
5. Run the relevant checks after implementing a phase.
6. Never silently ignore an error. Diagnose the root cause, then fix it.
7. Do not advance a phase until its verification checklist passes.
8. Git commit at the end of each major phase, after reviewing `git diff`.

### Data honesty
9. Never invent dataset columns, tables or relationships.
10. Never fabricate business results or metrics.
11. Never substitute fake data when real dataset data is available.
12. Never claim the dataset is genuinely Indian. See "Dataset provenance" below.

### Secrets
13. Secrets live in `.env`, never in source. `.env` is git-ignored.
14. `.env.example` is committed and contains keys with empty/placeholder values.
15. Never hardcode API keys, passwords, hostnames or tokens.

### Stack — do not extend without an explicit request
- PostgreSQL 18 (production database, source of truth)
- SQLAlchemy (database connectivity)
- Pydantic (API validation)
- FastAPI (backend)
- Streamlit (frontend/dashboard)
- Pandas / NumPy / scikit-learn / XGBoost (only where justified)
- Docker (reproducible deployment)

**Explicitly forbidden unless requested:** React, Node.js, Kubernetes, Redis,
microservices, message queues, any additional infrastructure.

### Layering
Keep database, analytics, ML and API layers cleanly separated.
- SQL does business aggregation.
- Pandas does analysis and feature engineering.
- Do not pull whole tables into memory when SQL can aggregate.

### AI safety (hard requirements)
The LLM must never execute arbitrary SQL. The natural-language-to-SQL path must:
- connect through a dedicated **read-only** database user
- allow only `SELECT` / `WITH` (CTE) read queries
- reject `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`,
  `CREATE`, and multi-statement input
- validate generated SQL before execution
- enforce a row limit and a statement timeout
- never expose database credentials or raw driver errors to the user
- return understandable error messages

### RAG
- Add only after the core application works.
- Do not use RAG where plain SQL is the better tool.
- Keep structured (SQL) retrieval separate from document retrieval.
- Cite retrieved documents in the UI where practical.

## Dataset provenance — state this accurately

The dataset is a **public Brazilian e-commerce dataset (Olist) that has been
relabelled with Indian city and state names**. Evidence:
- `GEO_LOCATION` latitude/longitude are Brazilian (São Paulo ≈ -23.55, -46.64),
  while the city/state text says Andhra Pradesh, Kerala, etc.
- `payment_type` contains `UPI` substituted for the Brazilian `boleto`.
- Product categories are English translations of the Olist category set.

**Therefore:** describe it as "a public e-commerce dataset with anonymised /
relabelled geography". Do not claim Indian provenance. Do not claim the currency
is INR — the source is Brazilian Real. Report money in **unspecified currency
units** unless the user decides otherwise.

## Learning requirement

After every major implementation, provide a **concise** briefing:
- what was built
- how the components communicate
- which files matter
- how to manually test it
- any important backend/database concept involved
- 3–5 interview questions the developer should be able to answer

Keep it practical. Not a tutorial. No walls of text.

## Environment facts (verified 2026-08-14)

| Thing | Value |
|---|---|
| OS | Windows 11, shell is Git Bash |
| Python | 3.11.0 at `C:\Users\LENOVO\AppData\Local\Programs\Python\Python311\python.exe` |
| pandas / numpy | 2.3.0 / 2.1.3 (system-wide; project will use its own `.venv`) |
| PostgreSQL | 18.6 at `C:\Program Files\PostgreSQL\18\bin`, running on `localhost:5432` |
| psql on PATH | No — invoke by full path, or add to PATH |
| Docker | Not installed |
| Git | 2.52.0 |
| Repo initialised | Not yet (Phase 1) |

Re-verify these rather than trusting this table if something behaves oddly.
