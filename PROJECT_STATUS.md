# Project Status

**Last updated:** 2026-08-14
**Current phase:** Phase 1 — Dataset audit (complete)
**Next phase:** Phase 2 — Repository & database foundation (schema + load)

---

## Phase board

| # | Phase | Status |
|---|---|---|
| 0 | Discovery & initial profiling | ✅ Complete |
| 1 | Dataset audit + documentation | ✅ Complete |
| 2 | Repo scaffold, env, PostgreSQL schema, data load | ⬜ Not started |
| 3 | SQL analytics layer | ⬜ Not started |
| 4 | Python analytics & feature engineering | ⬜ Not started |
| 5 | ML: segmentation, experience risk, forecasting | ⬜ Not started |
| 6 | FastAPI backend | ⬜ Not started |
| 7 | Streamlit dashboard | ⬜ Not started |
| 8 | LLM analyst + safe NL→SQL | ⬜ Not started |
| 9 | RAG knowledge assistant | ⬜ Not started |
| 10 | AI business recommendations | ⬜ Not started |
| 11 | Test suite hardening | ⬜ Not started |
| 12 | Docker | ⬜ Not started |
| 13 | Deployment & final documentation | ⬜ Not started |

Legend: ⬜ not started · 🟡 in progress · ✅ complete · ⛔ blocked

---

## Phase 1 — Dataset audit ✅

**Deliverables produced**

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

## Phase 2 — Repo scaffold, env, PostgreSQL schema, data load ⬜

**Planned work (details finalised at phase start)**

1. `git init`, `.gitignore`, `.env.example`, project folder structure.
2. Create `.venv`, pin dependencies in `requirements.txt`.
3. Create database `ecommerce_bi`, application user, **read-only** user.
4. Write DDL: 8 tables, PKs, FKs, indexes (per database_relationships.md).
5. Load CSVs with timestamp casting, product-name typo fix, review dedup, `unknown` category fill.
6. Run integrity tests.

**Verification checklist**

- [ ] `git status` clean, `.env` ignored, no secrets in tracked files
- [ ] Fresh `.venv` install from `requirements.txt` succeeds
- [ ] All 8 tables exist with declared PKs and FKs
- [ ] Row counts in PostgreSQL match the audit exactly (reviews = 99,441 after dedup)
- [ ] All FK constraints validate with zero violations
- [ ] Timestamp columns are true `TIMESTAMP`
- [ ] Read-only user can `SELECT` but is denied `INSERT`/`UPDATE`/`DELETE`
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
| DQ-1 | Reviews padded to 100,000 rows | Phase 2 (dedup keep-latest) |
| DQ-3 | 775 order-item-less orders | Phase 3 (join semantics) |
| DQ-4 | `shipping_limit_date` extends to 2020 | Phase 4 (exclude from features) |
| DQ-5 | Truncated sales tail | Phase 5 (trim forecast window) |
| DQ-6 | Zip codes without GEO_LOCATION rows | Phase 2 (LEFT JOIN only) |
| DQ-7 | 623 products missing category | Phase 2 (`unknown`) |
| DQ-10 | No churn signal | Locked in scope (D-007) |
| DQ-13 | Timestamps as text | Phase 2 (cast at load) |
| DQ-15 | Payment/item-total mismatches | Phase 3 (choose GMV vs paid per query) |
