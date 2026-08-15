# Project Status

**Last updated:** 2026-08-15
**Current phase (ORIGINAL roadmap):** Phase 12 — Docker / Deployment (✅ Full stack running in containers; 230/230 pytest cases still green)
**Next phase (ORIGINAL roadmap):** Phase 13 — Cloud deployment & final documentation

> Note on numbering: earlier in-session work labelled the pytest-hardening step "Phase 9" — that
> was numbering drift, not a change to the original roadmap. Per the original roadmap, all
> testing / security / production-readiness work is **Phase 11**. Docker belongs to a later phase
> and is deliberately out of scope here.

---

## Phase board

| # | Phase | Status |
|---|---|---|
| 0 | Discovery & initial profiling | ✅ Complete |
| 1 | Dataset audit, database foundation & initial warehouse load | ✅ Complete |
| 2 | SQL analytics layer | ✅ Complete |
| 3 | ML & Advanced Analytics | ✅ Complete |
| 4 | FastAPI backend | ✅ Complete |
| 5 | Streamlit dashboard | ✅ Complete |
| 6 | LLM analyst + safe NL→SQL | ✅ Complete |
| 6.1 | NL→SQL hardening + direct SQL editor | ✅ Complete (Gemini live path pending quota reset) |
| 7 | RAG knowledge assistant | ✅ Implemented & tested (Gemini synthesis pending quota) |
| 8 | AI business recommendations | ✅ Implemented & tested (Gemini reasoning pending quota) |
| 11 | Testing, Security & Production Readiness (ORIGINAL) | ✅ Complete — pytest 230/230 (100%) |
| 12 | Docker / Deployment (ORIGINAL) | ✅ Complete — 3-service stack builds + starts + passes end-to-end validation |
| 9 | Test suite hardening | ⬜ Not started |
| 10 | Docker | ⬜ Not started |
| 11 | Deployment & final documentation | ⬜ Not started |

## Phase 7 — RAG knowledge assistant (implemented 2026-08-15)

**Implemented**

- `rag/ingest.py` — heading-aware markdown chunker (min 20 / target 220 / max 400 words), MD5 fingerprint dedup, TF-IDF fit, pickled index in `rag/index/`.
- `rag/retriever.py` — cosine-similarity `Retriever` with `min_score` threshold, monotone-descending results, backend-swap-ready.
- `rag/synthesizer.py` — Gemini grounded synthesis with `system_instruction` + auto source footer; deterministic citation-only fallback when Gemini is absent/quota-exhausted (never fabricates).
- `scripts/build_rag_index.py` — one-shot ingest command.
- `dashboard.py` — new **📚 Ask the Knowledge Base (RAG)** section under the AI Analyst chat with sample questions, top-K selector, retrieved excerpts panel with per-chunk citations.

**Knowledge sources ingested**

- `docs/data_dictionary.md` (11 chunks)
- `docs/data_quality_report.md` (21 chunks)
- `docs/database_relationships.md` (9 chunks)
- `DECISIONS.md` (10 chunks)

Total: 51 chunks, 3,112-term vocabulary.

**Tested — `scripts/test_rag_pipeline.py`**

- 21/21 passed (100%).
- Coverage: ingestion, chunking bounds, dedup, index round-trip, source/section propagation, retrieval relevance (GMV, negative-review rule, churn decision, provenance, safety controls), score monotonicity, empty query, off-topic query relevance ceiling, min-score filter, fallback citation propagation, prompt-leak check, stats sources coverage, ingestion minimum-chunk floor.

**Regression — `scripts/test_sql_editor_100.py`**

- 111/111 passed (100%). No Phase 6 regression.

**Pending / not verified**

- Live Gemini synthesis (`_try_gemini` path) — not exercised because the free-tier `gemini-3.6-flash` daily quota (20 req/day) is currently exhausted. Deterministic fallback is exercised and passing. Live path will be tested tomorrow after quota reset.
- No FastAPI endpoint wrapper for RAG yet (dashboard uses `rag/*` directly; add `/api/rag` in a follow-up if the API layer needs it).

## Phase 8 — AI business recommendations (implemented 2026-08-15)

**Implemented**

- `ai/decision_support.py` — evidence-first recommendation engine:
  - `Evidence` and `RecommendationPackage` dataclasses (every fact traceable to a source view + validated SQL).
  - Keyword router → 7 recommendation categories, each with its own SQL query builder using existing `analytics.*` views: `category_quality`, `product_risk`, `delivery_sla`, `review_health`, `sales_trend`, `customer_segments`, `kpi_health`.
  - All SQL passes through `ai.sql_validator.validate_sql` before executing on `readonly_engine`. No new database access surface.
  - Optional RAG lookup per template via `rag.Retriever` (silent no-op when index missing).
  - Optional Gemini reasoning layer that only *rewrites* the deterministic package under a hard system prompt ("use only numbers in the evidence"). If Gemini is unavailable or fails, the deterministic package is what the user sees. Never fabricates.
  - `RecommendationPackage.to_markdown()` emits the required sections: Recommendation / Why / Observation / Context / Interpretation / Evidence / Limitations / Sources.
- `dashboard.py` — new **💡 AI Business Recommendations** section under RAG, with sample-question buttons, Gemini toggle, routed-category label, and a structured evidence table.

**Tested — `scripts/test_decision_support.py`**

- 25/25 passed (100%), Gemini disabled for determinism.
- Coverage: routing all 7 categories + unsupported, evidence integrity (metrics, sources, MoM computation, product/category-specific fields), hallucination guard (no numbers in the recommendation text outside evidence/observation), markdown section completeness, unsupported-question message, SQL-injection input safety, RAG citation propagation, confidence value normalization, all 7 categories reachable, evidence JSON-serializability, mode reporting.

**Phase 6 regression — `scripts/test_sql_editor_100.py`**: 111/111 (100%). No regression.

**Phase 7 regression — `scripts/test_rag_pipeline.py`**: 21/21 (100%). No regression.

**Pending / not verified**

- Live Gemini reasoning-layer output — not exercised (quota exhausted). Deterministic path is fully exercised and passing.

Legend: ⬜ not started · 🟡 in progress · ✅ complete · ⛔ blocked

---

## Phase 1 — Dataset audit, database foundation & initial warehouse load 🟡

**Completed deliverables — dataset audit**

- `scripts/audit_dataset.py` — a reusable audit (row counts, dtypes, nulls,
  duplicates, PK/FK integrity, categorical distributions, numeric percentiles,
  date ranges, reconciliation checks). Rerun any time: `python scripts/audit_dataset.py`.
- `docs/audit_output.txt` — captured run of the above.
- [docs/data_dictionary.md](docs/data_dictionary.md) — every table and column,
  types, nulls, cardinality, load type, cleaning notes.
- [docs/data_quality_report.md](docs/data_quality_report.md) — 18 issues rated
  by severity, each with evidence and remediation plan.
- [docs/database_relationships.md](docs/database_relationships.md) — ERD, PKs,
  FKs (all verified zero-orphan), lookup joins, canonical join recipes, load order,
  index plan.

**What the audit found (summary — full detail in the reports)**

- All 8 tables load cleanly. All 6 declared foreign keys have **zero orphans**.
- PKs identified for every table; the reviews table only has a clean PK **after
  dedup** (raw file has 100,000 rows for 99,441 orders — see DQ-1).
- Sales window is healthy from **2017-01 → 2018-08** (20 months). The tail is
  truncated; forecasting must trim.
- **Churn is not modellable** — 3.12% repeat-purchase rate. Replaced by
  experience-risk (negative-review) modelling.
- Geography is relabelled Olist (Brazilian coordinates, Indian labels). No map
  visualisations; currency stays as unspecified units.
- 249 orders have payment / item-total mismatches > 1.00 unit (0.25%). GMV vs
  cash-received queries need to pick a lane per DQ-15.

**Verification** — every number produced by running code against the real CSVs.

## Features by supportability (locked in Phase 1)

**Statistically defensible from this data**

| Feature | Basis |
|---|---|
| Revenue / GMV / AOV analytics by day-month-quarter, category, state, seller, payment type | 98,666 order-items × 20 clean months |
| Delivery-performance analytics (days-to-deliver, on-time rate, delay distribution, state SLAs) | 96,470 delivered orders with parsed dates |
| RFM customer segmentation | 96,096 unique customers with recency/frequency/monetary derivable |
| Customer-experience-risk model (predict `review_score ≤ 2`) | 15,093 negatives out of 99,441 → 15.09%, clean class balance |
| Monthly sales forecasting (2017-01..2018-08 window) | 20 continuous months of stable order volume |
| Seller performance scorecards | 3,095 sellers, all active, joined to reviews and delivery |
| Product-category performance & pareto analysis | 71 categories, 32,951 products |
| Payment-mix analysis | 5 payment types, installments 1..24 |
| Natural-language → SQL over the warehouse | Small enough schema to fit in a prompt, well-typed after load |
| RAG over project documentation | Docs live in `docs/`, will grow |

**Not defensible from this data** (locked as out of scope)

| Feature | Why not |
|---|---|
| **Customer churn** | Only 3.12% of customers ever placed a second order (DQ-10). Labels collapse to ~97% one class. |
| **Customer lifetime value** | Same reason — no repeat purchases means no observable lifetime. |
| **Cohort retention curves** | No repeat cohorts to track. |
| **Review-text NLP / sentiment / topic modelling** | No text columns exist (DQ-14). Only the numeric score. |
| **Profit / margin analytics** | No cost or acquisition-cost data. |
| **Geographic map visualisations** | Brazilian coordinates under Indian labels (DQ-2). |
| **True Indian-market narrative** | Provenance is relabelled Olist; claim would be factually wrong. |
| **Currency-specific INR reporting** | Same. Reported in unspecified units. |
| **Cross-session user identification / anonymous funnel** | No session, clickstream or page-view data. |
| **Recommendation engines that depend on repeated interactions per user** | 3.12% repeats. Content-based / co-purchase within an order is fine; user-based collaborative filtering is not. |

---

## Remaining Phase 1 work — safe database initialization & data load ⬜

**Planned work (details finalised at phase start)**

1. Complete the dedicated-role bootstrap against the manually created `ecommerce_ai` database; it must never create/drop a database.
2. Run create-only DDL: 8 tables, PKs, FKs and indexes (per database_relationships.md).
3. Load CSVs with timestamp casting, product-name typo fix, review dedup and `unknown` category fill.
4. Run read-only integrity and privilege validation.

**Verification checklist**

- [ ] `git status` clean, `.env` ignored, no secrets in tracked files
- [ ] Fresh `.venv` install from `requirements.txt` succeeds
- [ ] All 8 tables exist with declared PKs and FKs
- [ ] Row counts in PostgreSQL match the audit exactly (reviews = 99,441 after dedup)
- [ ] All FK constraints validate with zero violations
- [ ] Timestamp columns are true `TIMESTAMP`
- [ ] Read-only user can `SELECT`; privilege metadata confirms it lacks `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE`
- [ ] Data-integrity `pytest` suite green

**Expected row counts**

| Table | Rows |
|---|---:|
| customers | 99,441 |
| geo_location | 19,015 |
| orders | 99,441 |
| order_items | 112,650 |
| order_payments | 103,886 |
| order_reviews (after dedup) | 99,441 |
| products | 32,951 |
| sellers | 3,095 |

---

## Phases 3–13

Each will be planned in detail at phase start per CLAUDE.md rule 2 (inspect
current state first).

---

## Known issues carried forward

Referenced by ID from the quality report. All are addressed by Phase 2 unless
noted.

| ID | Issue | Address in |
|---|---|---|
| DQ-1 | Reviews padded to 100,000 rows | Phase 1 (dedup keep-latest) |
| DQ-3 | 775 order-item-less orders | Phase 2 (join semantics) |
| DQ-4 | `shipping_limit_date` extends to 2020 | Phase 3 (exclude from features) |
| DQ-5 | Truncated sales tail | Phase 4 (trim forecast window) |
| DQ-6 | Zip codes without GEO_LOCATION rows | Phase 1 (LEFT JOIN only) |
| DQ-7 | 623 products missing category | Phase 1 (`unknown`) |
| DQ-10 | No churn signal | Locked in scope (D-007) |
| DQ-13 | Timestamps as text | Phase 1 (cast at load) |
| DQ-15 | Payment/item-total mismatches | Phase 2 (choose GMV vs paid per query) |
