# Data Dictionary

Source: `Dataset/raw/*.csv`, extracted from `Dataset/archive (4).zip`.
All facts here are produced by `scripts/audit_dataset.py`; re-run it to refresh
the numbers. **Do not modify the raw CSVs.**

Legend for the "Load type" column: the PostgreSQL type used in Phase 1 loading.
`SMALLINT` is chosen wherever the value fits, to keep the warehouse compact.

---

## Tables at a glance

| Table | Rows | Grain (one row = ...) | Load? |
|---|---:|---|---|
| CUSTOMERS | 99,441 | one *order-scoped* customer record | ✅ |
| GEO_LOCATION | 19,015 | one zip prefix (already deduplicated) | ✅ |
| ORDERS | 99,441 | one order | ✅ |
| ORDER_ITEMS | 112,650 | one line of one order | ✅ |
| ORDER_PAYMENTS | 103,886 | one payment record on an order | ✅ |
| ORDER_REVIEW_RATINGS | 100,000 | one review row (padded — see quality report) | ✅ (deduped) |
| PRODUCTS | 32,951 | one product SKU | ✅ |
| SELLERS | 3,095 | one seller | ✅ |

---

## CUSTOMERS

Grain: **one row per `customer_id`.** A `customer_id` is *order-scoped* — the
same person placing two orders appears as two `customer_id`s but one
`customer_unique_id`. Any customer-level analysis (RFM, segmentation, repeat
rate) must group by `customer_unique_id`.

| Column | CSV dtype | Load type | Nulls | Distinct | Notes / example |
|---|---|---|---:|---:|---|
| `customer_id` | object | `CHAR(32)` PK | 0 | 99,441 | 32-char hex hash. Example `06b8999e2fba1a1fbc88172c00ba8bc7`. |
| `customer_unique_id` | object | `CHAR(32)` | 0 | 96,096 | Person-level identifier. 3.12% of people appear more than once. |
| `customer_zip_code_prefix` | int64 | `INTEGER` | 0 | 14,994 | Range 1,003–99,990. Leading zeros are lost in CSV; keep as integer. FK-lookup to `GEO_LOCATION.geolocation_zip_code_prefix` but not enforced (278 orphans). |
| `customer_city` | object | `TEXT` | 0 | 4,119 | Free text. 95.04% agree with GEO_LOCATION on the same zip. |
| `customer_state` | object | `CHAR(2..)` / `TEXT` | 0 | 20 | See "Relabelled geography" in DECISIONS.md D-002. |

---

## GEO_LOCATION

Grain: **one row per zip prefix.** The source Olist file has ~1M rows; the copy
in this dataset has already been reduced to one representative row per prefix
(19,015 rows, all unique).

| Column | CSV dtype | Load type | Nulls | Notes |
|---|---|---|---:|---|
| `geolocation_zip_code_prefix` | int64 | `INTEGER` PK | 0 | 1,003–99,990. |
| `geolocation_lat` | float64 | `NUMERIC(10,7)` | 0 | Range −36.6054 → 42.1840. Brazilian coordinates. |
| `geolocation_lng` | float64 | `NUMERIC(10,7)` | 0 | Range −72.9273 → 121.1054. |
| `geolocation_city` | object | `TEXT` | 0 | |
| `geolocation_state` | object | `TEXT` | 0 | 20 values. |

---

## ORDERS

Grain: **one row per `order_id`.**

| Column | CSV dtype | Load type | Nulls (%) | Notes |
|---|---|---|---:|---|
| `order_id` | object | `CHAR(32)` PK | 0 | Unique. |
| `customer_id` | object | `CHAR(32)` FK → CUSTOMERS | 0 | 99,441 distinct = one-to-one with orders. |
| `order_status` | object | `VARCHAR(16)` | 0 | 8 values: `delivered` (96,478), `shipped` (1,107), `canceled` (625), `unavailable` (609), `invoiced` (314), `processing` (301), `created` (5), `approved` (2). |
| `order_purchase_timestamp` | object (text) | `TIMESTAMP` | 0 | Format `M/D/YYYY H:MM`. Range 2016-09-04 → 2018-10-17. |
| `order_approved_at` | object (text) | `TIMESTAMP NULL` | 0.16% | Null for orders that never cleared payment approval. |
| `order_delivered_carrier_date` | object (text) | `TIMESTAMP NULL` | 1.79% | Handoff to carrier. |
| `order_delivered_customer_date` | object (text) | `TIMESTAMP NULL` | 2.98% | End-of-journey. 8 orders show `delivered` status but null date. |
| `order_estimated_delivery_date` | object (text) | `TIMESTAMP` | 0 | Day-precision (00:00). |

**Derived columns to compute at load or in views:**
`days_to_deliver`, `delay_vs_estimate`, `approval_lag_hours`.

---

## ORDER_ITEMS

Grain: **one row per line of an order.** Same order can have up to 21 items.

| Column | CSV dtype | Load type | Nulls | Notes |
|---|---|---|---:|---|
| `order_id` | object | `CHAR(32)` PK part, FK → ORDERS | 0 | |
| `order_item_id` | int64 | `SMALLINT` PK part | 0 | Line number 1..21 within the order. |
| `product_id` | object | `CHAR(32)` FK → PRODUCTS | 0 | |
| `seller_id` | object | `CHAR(32)` FK → SELLERS | 0 | |
| `shipping_limit_date` | object (text) | `TIMESTAMP` | 0 | Seller SLA date. Extends to 2020-04-09 for some records — **do not treat as an event date**. |
| `price` | float64 | `NUMERIC(10,2)` | 0 | 0.85 – 6,735.00. Median 74.99, 99th pct 890.00. |
| `freight_value` | float64 | `NUMERIC(10,2)` | 0 | 0.00 – 409.68. Median 16.26. |

Composite PK: `(order_id, order_item_id)`.

---

## ORDER_PAYMENTS

Grain: **one row per payment record on an order.** One order may have several
payments (multiple vouchers + a card, etc.). `payment_sequential` orders them.

| Column | CSV dtype | Load type | Nulls | Notes |
|---|---|---|---:|---|
| `order_id` | object | `CHAR(32)` PK part, FK → ORDERS | 0 | |
| `payment_sequential` | int64 | `SMALLINT` PK part | 0 | 1..29. |
| `payment_type` | object | `VARCHAR(16)` | 0 | `credit_card` (76,795), `UPI` (19,784, relabelled from Olist `boleto`), `voucher` (5,775), `debit_card` (1,529), `not_defined` (3). |
| `payment_installments` | int64 | `SMALLINT` | 0 | 0..24. 2 rows have 0 installments — encode as `NULL` or leave as-is per validator rule. |
| `payment_value` | float64 | `NUMERIC(10,2)` | 0 | 0.00 – 13,664.08. 9 rows are exactly 0.00. |

Composite PK: `(order_id, payment_sequential)`.

---

## ORDER_REVIEW_RATINGS

Grain: intended to be **one row per order**, but the file is padded — see the
quality report, DQ-1.

| Column | CSV dtype | Load type | Nulls | Notes |
|---|---|---|---:|---|
| `review_id` | object | `CHAR(32)` | 0 | Not unique: 827 duplicated. |
| `order_id` | object | `CHAR(32)` FK → ORDERS | 0 | Not unique: 559 duplicated. |
| `review_score` | int64 | `SMALLINT` | 0 | Values 1..5. Distribution: 5★ 57.4%, 4★ 19.2%, 3★ 8.3%, 2★ 3.2%, 1★ 11.9%. |
| `review_creation_date` | object (text) | `TIMESTAMP` | 0 | Day-precision. |
| `review_answer_timestamp` | object (text) | `TIMESTAMP` | 0 | When the seller/marketplace answered. |

**PK strategy at load time:** deduplicate to one row per `order_id` keeping the
latest `review_answer_timestamp`; the resulting table has `order_id` as the
primary key. See DQ-1 for the rationale.

**No review text columns exist.** Any feature described as "review text
sentiment" would be fabricated.

---

## PRODUCTS

Grain: **one row per `product_id`.**

| Column | CSV dtype | Load type | Nulls | Notes |
|---|---|---|---:|---|
| `product_id` | object | `CHAR(32)` PK | 0 | |
| `product_category_name` | object | `VARCHAR(64)` | 623 (1.89%) | 71 categories (English). Load nulls as `unknown`. |
| `product_name_lenght` | float64 | `SMALLINT NULL` | 610 (1.85%) | Source spelling; kept for fidelity. 5..76. |
| `product_description_lenght` | float64 | `INTEGER NULL` | 610 | 4..3,992. |
| `product_photos_qty` | float64 | `SMALLINT NULL` | 610 | 1..20. |
| `product_weight_g` | float64 | `INTEGER NULL` | 2 | 0..40,425. Values of 0 are suspect; treat as null in features. |
| `product_length_cm` | float64 | `SMALLINT NULL` | 2 | 7..105. |
| `product_height_cm` | float64 | `SMALLINT NULL` | 2 | 2..105. |
| `product_width_cm` | float64 | `SMALLINT NULL` | 2 | 6..118. |

Rename in Phase 1: `product_name_lenght → product_name_length`, same for
description. Keep the raw file column names for the loader.

---

## SELLERS

Grain: **one row per `seller_id`.**

| Column | CSV dtype | Load type | Nulls | Notes |
|---|---|---|---:|---|
| `seller_id` | object | `CHAR(32)` PK | 0 | |
| `seller_zip_code_prefix` | int64 | `INTEGER` | 0 | FK-lookup to `GEO_LOCATION` — 7 orphans, do not constrain. |
| `seller_city` | object | `TEXT NULL` | 57 (1.84%) | |
| `seller_state` | object | `TEXT NULL` | 57 | 19 values. |

---

## Column naming conventions

- Raw CSV columns are loaded as-is where they are already snake_case.
- Two typos in the source (`product_name_lenght`, `product_description_lenght`)
  are corrected during load; the raw file is not touched.
- All timestamps are cast from `M/D/YYYY H:MM` text to `TIMESTAMP` at load time.
