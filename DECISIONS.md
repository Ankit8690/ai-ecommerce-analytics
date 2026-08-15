# Architecture Decision Record

Each entry: the decision, why, what was rejected, and the consequence.
Append new decisions; do not rewrite history. Mark superseded entries clearly.

---

### D-001 — Project root is `E:\AI_E-Commerce_Analytics`
**Date:** 2026-08-14 · **Status:** Accepted

The alternative was the configured primary working directory
`C:\Users\LENOVO\OneDrive\Desktop\claude_testing`. Rejected because it sits
inside OneDrive: continuous sync of `.venv`, `__pycache__`, model artifacts and
database dumps causes file locks during `pip install` and slows every operation.
The E: folder is already named for this project and holds the dataset.

**Consequence:** all paths in this repo are relative to `E:\AI_E-Commerce_Analytics`.

---

### D-002 — Dataset described as "public e-commerce dataset with relabelled geography"
**Date:** 2026-08-14 · **Status:** Accepted

The data is the Olist Brazilian e-commerce dataset with Indian place names
substituted. Proven by three independent signals: geolocation coordinates are
Brazilian (São Paulo ≈ -23.55, -46.64) while labels read "Andhra Pradesh";
`payment_type` has `UPI` where Olist has `boleto`; product categories are English
translations of the Olist category list.

Claiming Indian provenance in a portfolio project would be a factual error an
interviewer could catch. Claiming Brazilian provenance in the UI would contradict
the visible labels.

**Consequence:** README and dashboard describe it as a public dataset with
anonymised/relabelled geography. Currency is shown as **unspecified units**, never
₹ or R$. No geographic map is plotted against an India basemap (see D-008).

---

### D-003 — PostgreSQL 18 is the single source of truth
**Date:** 2026-08-14 · **Status:** Accepted

Verified installed at `C:\Program Files\PostgreSQL\18\bin`, accepting connections
on `localhost:5432`. `psql` is not on PATH; invoke by full path.

SQLite was rejected: the project requires PostgreSQL as production, and starting
on SQLite would create migration work plus divergent SQL dialect behaviour
(window functions, `DATE_TRUNC`, permission model) that this project depends on.

**Consequence:** the read-only-user safety model in D-006 is available, because
it is a real database-level privilege rather than an application convention.

---

### D-004 — CSVs load into PostgreSQL once in Phase 1; analytics query the database
**Date:** 2026-08-14 · **Status:** Accepted

Raw CSVs are treated as an immutable input, loaded once during Phase 1 (which
also includes schema creation and load validation). All downstream layers read
from PostgreSQL, not from `Dataset/raw/`.

Rejected: reading CSVs directly in Streamlit/FastAPI. That would make the
database decorative and defeat the point of the project.

**Consequence:** aggregation happens in SQL; Pandas receives already-reduced
result sets. Full tables are never loaded into memory for dashboard queries.

---

### D-005 — Four separated layers
**Date:** 2026-08-14 · **Status:** Accepted

```
PostgreSQL  →  SQL analytics  →  Python analytics/ML  →  FastAPI  →  Streamlit
```

Each layer depends only on the one below it. Streamlit never talks to the
database directly; it calls FastAPI over HTTP.

Rejected: letting Streamlit query PostgreSQL directly. It is simpler short-term
but collapses the API layer, which is the part that demonstrates backend skill —
the developer's stated learning goal.

**Consequence:** one extra hop in local development, and the API must be running
for the dashboard to work. Accepted deliberately.

---

### D-006 — LLM SQL access goes through a read-only user plus a validator
**Date:** 2026-08-14 · **Status:** Accepted

Two independent controls, because either alone is insufficient:
1. **Database privilege** — a dedicated PostgreSQL role with `SELECT` only. This
   is the control that actually holds if the validator is bypassed.
2. **Application validator** — parse and allowlist generated SQL: permit only a
   single `SELECT`/`WITH` statement; reject DDL/DML keywords, multiple statements
   and comment-based injection; enforce `LIMIT` and a statement timeout.

Rejected: prompt-only safety ("please only write SELECT"). Prompt instructions
are not a security boundary.

**Consequence:** the AI analyst cannot mutate data even if the model is
manipulated. Errors are translated into plain messages; raw driver errors and
connection strings are never surfaced.

---

### D-007 — Churn modelling is out of scope; experience-risk replaces it
**Date:** 2026-08-14 · **Status:** Accepted

Measured: 96,096 unique customers, of whom 2,997 (3.12%) ever placed more than
one order. There is no meaningful repeat-purchase signal, so a churn label would
be ~97% "churned" and the model would learn nothing. Building it anyway would be
fabricating a business result.

Instead the project models **customer experience risk** — predicting a negative
review (score ≤ 2) from delivery, freight, price, payment and category features.
This is genuinely supported: 15,093 negative reviews (15.1%) and 8.11% late
deliveries give real class balance and real signal.

**Consequence:** the README states plainly why churn was not modelled. This is a
stronger interview answer than a meaningless churn model.

---

### D-008 — No geographic map visualisation
**Date:** 2026-08-14 · **Status:** Accepted

Coordinates are Brazilian, labels are Indian. Plotting them on either basemap
produces a visibly wrong chart. State-level bar charts using the labels are fine,
because they treat the state as an opaque category.

**Consequence:** geography appears as ranked categorical charts, not maps.

---

### D-009 — LLM provider deferred to Phase 7
**Date:** 2026-08-14 · **Status:** Open

The LLM layer will be written behind a thin provider-agnostic interface so the
choice can be made at Phase 7 without rework. Model name, API key and base URL
come from environment variables.

**Consequence:** no provider SDK is added to `requirements.txt` until Phase 7.
