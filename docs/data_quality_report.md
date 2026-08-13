# Data Quality Report

Every fact here was produced by `scripts/audit_dataset.py` against the raw CSVs
in `Dataset/raw/`. Full output is captured in `docs/audit_output.txt`.

Severity: **High** blocks a downstream feature or misleads users if ignored ·
**Medium** distorts a metric or a model but is fixable · **Low** cosmetic or
edge-case.

---

## Summary

| ID | Issue | Severity | Where fixed |
|---|---|---|---|
| [DQ-1](#dq-1) | Review table is padded — 100,000 rows for 99,441 orders | High | Phase 1 loader (dedup keep-latest) |
| [DQ-2](#dq-2) | Geography relabelled: Brazilian coordinates, Indian labels | High | README + DECISIONS.md D-002/D-008; no map plots |
| [DQ-3](#dq-3) | 775 orders have no line items; 1 order has no payment | Medium | Left as-is; queries use `LEFT JOIN`s |
| [DQ-4](#dq-4) | `shipping_limit_date` extends to 2020, past the order window | Medium | Excluded from time-based features |
| [DQ-5](#dq-5) | Sales tail truncated (2018-09: 16 orders; 2018-10: 4) | Medium | Forecast window trims to 2017-01..2018-08 |
| [DQ-6](#dq-6) | 278 customer zips + 7 seller zips missing from GEO_LOCATION | Low | FK not enforced |
| [DQ-7](#dq-7) | 623 products have no `product_category_name` | Low | Load as `unknown` |
| [DQ-8](#dq-8) | 9 payments = 0.00 · 3 `not_defined` · 2 orders with 0 installments | Low | Left as-is, documented |
| [DQ-9](#dq-9) | 8 orders marked `delivered` but no `order_delivered_customer_date` | Low | Delivery features skip them |
| [DQ-10](#dq-10) | Repeat purchase only 3.12% — no meaningful churn signal | High | Churn removed from scope (D-007) |
| [DQ-11](#dq-11) | 4.96% city / 2.36% state disagreement between CUSTOMERS and GEO | Low | Prefer CUSTOMERS labels; GEO used only for coordinates |
| [DQ-12](#dq-12) | Two column-name typos: `product_name_lenght`, `product_description_lenght` | Low | Renamed at load time |
| [DQ-13](#dq-13) | All timestamps stored as text | Medium | Cast to `TIMESTAMP` at load |
| [DQ-14](#dq-14) | No review text columns | High for scope | "Review sentiment" is out of scope |
| [DQ-15](#dq-15) | 249 orders have payments and item totals differing by >1.00 unit | Low | Documented; use `payment_value` for revenue-received analyses, `price+freight` for GMV |
| [DQ-16](#dq-16) | Free-text city fields have 4,119 distinct values | Low | Analyze by state, not city |
| [DQ-17](#dq-17) | Seller `city`/`state` null for 57 of 3,095 sellers | Low | Report as `unknown` |
| [DQ-18](#dq-18) | Extreme freight-to-price ratios (99th pct 1.55, max 26.2) | Low | Cap in visualisations, not in raw table |

---

### DQ-1 — The review table is padded

**Evidence.**
- `ORDER_REVIEW_RATINGS` has **100,000 rows** covering exactly the same **99,441 order_ids** as `ORDERS`.
- `review_id` appears more than once in **1,629 rows** (827 unique duplicated `review_id`s).
- `order_id` appears more than once in **1,114 rows** (559 unique duplicated `order_id`s).
- The original Olist review table is ~99,224 rows and does **not** cover every order.
- Only the pair `(review_id, order_id)` is unique.

**Severity: High.** If loaded as-is, every downstream query implying "% of orders reviewed" reads 100%. That is factually wrong and looks fabricated to anyone who has seen the source dataset.

**Fix (Phase 1 loader).** Deduplicate to one row per `order_id`, keeping the row with the latest `review_answer_timestamp` (ties broken by `review_creation_date`, then by `review_id`). Load with `order_id` as the primary key.

---

### DQ-2 — Relabelled geography

**Evidence.**
- Latitude range −36.6054 → 42.1840; longitude range −72.9273 → 121.1054. The bulk cluster around (−23.5, −46.6) — São Paulo.
- `customer_state` values are the 20 Indian states.
- `payment_type` includes `UPI` where the Olist source has `boleto`.

**Severity: High** because a chart plotting these coordinates over an India basemap would obviously be wrong, and any narrative claiming "insights into the Indian market" would be false.

**Fix.** README, DECISIONS.md D-002, and D-008 document this explicitly. Currency is reported in unspecified units. No geographic map is drawn. State-level bar charts are fine, treating the state as an opaque category.

---

### DQ-3 — 775 orders without items, 1 without payment

**Evidence.**
- Orders never appearing in `ORDER_ITEMS`: 775. Their statuses are mostly `unavailable`/`canceled`.
- Orders never appearing in `ORDER_PAYMENTS`: 1.

**Severity: Medium.** Any revenue query using `INNER JOIN ORDER_ITEMS` will drop 775 orders; that is usually correct (no items = no revenue) but must be a deliberate choice, not a coincidence.

**Fix.** All revenue and item-count queries use `INNER JOIN ORDER_ITEMS`; all order-status queries use the ORDERS table directly. Documented at the SQL layer in Phase 2.

---

### DQ-4 — `shipping_limit_date` extends past the sales window

**Evidence.** `min = 2016-09-19 00:15`, `max = 2020-04-09 22:35`. All order timestamps end by 2018-10.

**Severity: Medium** for feature engineering.

**Fix.** Do not use `shipping_limit_date` as an event date in time-series features. It is only meaningful relative to the order — expose it as `days_between_purchase_and_ship_limit` if needed.

---

### DQ-5 — Truncated sales tail

**Evidence.** Monthly order counts drop from 6,512 in 2018-08 to 16 in 2018-09 and 4 in 2018-10. That is not a demand collapse; it is a cut-off export.

**Severity: Medium** — including those months in a forecast would introduce a bogus downward trend.

**Fix.** Forecasting phase trims to the healthy window **2017-01 through 2018-08** (20 months). Documented at model-training time.

---

### DQ-6 — Zip codes without GEO_LOCATION rows

**Evidence.** 278 of 99,441 customer rows (0.28%) and 7 of 3,095 seller rows (0.23%) have a zip prefix with no matching row in `GEO_LOCATION`.

**Severity: Low.**

**Fix.** Do not declare a foreign key on the zip columns. Joins to `GEO_LOCATION` use `LEFT JOIN` and tolerate misses.

---

### DQ-7 — Products missing category

**Evidence.** 623 of 32,951 products (1.89%) have null `product_category_name`. The same 610 also have null name/description length and photo count.

**Severity: Low.**

**Fix.** Load nulls as literal `unknown` for `product_category_name` so grouping never drops rows. Length/photo fields stay nullable.

---

### DQ-8 — Suspicious payment values

**Evidence.**
- 9 payments have `payment_value = 0.00`.
- 3 payments have `payment_type = 'not_defined'`.
- 2 payments have `payment_installments = 0`.

**Severity: Low** — the counts are tiny and the payment reconciliation (DQ-15) is otherwise excellent.

**Fix.** Load as-is. Analytics that compute average installment size filter out `installments = 0`.

---

### DQ-9 — Delivered orders with null delivery date

**Evidence.** 8 orders have `order_status = 'delivered'` but a null `order_delivered_customer_date`.

**Severity: Low.**

**Fix.** `days_to_deliver` and `delay_vs_estimate` require both timestamps; those 8 orders naturally drop out.

---

### DQ-10 — No churn signal

**Evidence.** 96,096 unique customers; 2,997 (**3.12%**) placed more than one order. The max any single customer placed is 17.

**Severity: High for feature scoping.** A "churn / did not repurchase within N days" label would be ~97% one class. Building a churn model here would fabricate a business result.

**Fix.** Churn is removed from scope (DECISIONS.md D-007). Replaced by an **experience-risk** model that predicts negative reviews (`review_score ≤ 2`) — 15,093 examples (15.09%), which gives a defensible class balance.

---

### DQ-11 — City/state disagreement between CUSTOMERS and GEO_LOCATION

**Evidence.** Joining on `zip_code_prefix`: `customer_city == geolocation_city` in **95.04%** of rows; `customer_state == geolocation_state` in **97.64%**.

**Severity: Low.**

**Fix.** Prefer the labels in CUSTOMERS/SELLERS for anything user-facing. Use GEO_LOCATION only when coordinates are needed.

---

### DQ-12 — Typos in source column names

**Evidence.** `product_name_lenght`, `product_description_lenght`.

**Fix.** Renamed at load time to `product_name_length`, `product_description_length`. The CSV is not modified.

---

### DQ-13 — Timestamps stored as text

**Evidence.** Format `M/D/YYYY H:MM`, single-digit month and day, no leading zeros. All 5 order timestamps, the shipping limit, and both review timestamps.

**Fix.** Cast to `TIMESTAMP` at load time using `to_timestamp('MM/DD/YYYY HH24:MI', ...)` or a Python pre-parse. The audit confirms every row parses.

---

### DQ-14 — No review text

**Evidence.** `ORDER_REVIEW_RATINGS` has only `review_score` — no title, no message body.

**Fix.** Any feature described as "review text NLP / sentiment / topic extraction" is out of scope. The score is the only signal.

---

### DQ-15 — Payment vs item-total mismatches

**Evidence.** 98,665 orders can be compared: 99.42% match exactly (|diff| < 0.01). Only **249 orders** differ by more than 1.00 unit. Median diff is 0.00; extremes reach −51.62 / +182.81 (a handful of large orders with vouchers or refunds).

**Severity: Low.**

**Fix.** Reporting convention:
- **GMV** = Σ `ORDER_ITEMS.price` (+ freight when specified).
- **Revenue received** = Σ `ORDER_PAYMENTS.payment_value`.
Choose deliberately per query.

---

### DQ-16 — Free-text city cardinality

**Evidence.** 4,119 distinct customer cities, 534 distinct seller cities. Casing and spelling variants inflate the count.

**Fix.** Analyses group by `customer_state` / `seller_state` (20 / 19 values). City-level drill-down is available but not the default chart.

---

### DQ-17 — Seller city/state nulls

**Evidence.** 57 of 3,095 sellers (1.84%) have null `seller_city` and `seller_state`.

**Fix.** Display as `unknown`. Do not exclude from seller scorecards.

---

### DQ-18 — Extreme freight-to-price ratios

**Evidence.** Median 0.23; 95th pct 0.88; 99th pct 1.55; max 26.24 (freight worth 26× the item price, driven by tiny-price items like a 0.85 pen with 22 in freight).

**Fix.** In dashboards, cap the y-axis at a sensible percentile; do not clip the underlying data.

---

## Loader responsibilities (Phase 1)

The loader must produce a database that satisfies:

1. Row counts equal to those in the data dictionary (with the reviews table
   deduplicated to 99,441 rows keyed by `order_id`).
2. All timestamps are true `TIMESTAMP`, not text.
3. All declared foreign keys validate with zero violations.
4. `product_category_name` is never null.
5. Both typo columns are renamed.
6. Zip-based joins are `LEFT JOIN` (no FK constraint on GEO_LOCATION zip).
7. A dedicated read-only role exists and cannot `INSERT` / `UPDATE` / `DELETE`.

Each of these has a matching test in Phase 1's verification checklist.
