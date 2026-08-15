# Power BI / Tableau Integration Guide

The `analytics.*` schema in PostgreSQL is designed to be the single source of
truth for both the Streamlit dashboards **and** external professional BI tools.
Every metric shown to a user — inside Streamlit, through the FastAPI endpoints,
or in a Power BI / Tableau report — should ultimately trace back to one of the
views documented below. This prevents the same number from having different
definitions in different tools.

> **Rule.** BI tools consume the `analytics.*` views, not `public.*` raw tables.
> Business logic (GMV = price + freight, negative review = score ≤ 2, on-time
> vs late, RFM segmentation, etc.) lives once — in SQL — so Power BI DAX and
> Tableau LOD calcs stay thin.

---

## 1. Connection settings

### Local PostgreSQL (host install)

| Field | Value |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `ecommerce_ai` |
| User | `ecommerce_readonly` |
| Password | value of `READONLY_DB_PASSWORD` in your `.env` (never commit) |
| SSL Mode | `prefer` (or `disable` for local dev) |

### Dockerized PostgreSQL (Phase 12 compose stack)

By default the containerised Postgres is **not exposed** to the host — the
app services reach it inside the compose network. To connect Power BI /
Tableau from the host, uncomment the `ports:` block in `docker-compose.yml`:

```yaml
  postgres:
    # ...
    ports:
      - "5432:5432"
```

Then `docker compose up -d postgres` and connect exactly as in the local case.

### Read-only enforcement

Always use the **`ecommerce_readonly`** role — never `postgres` or
`ecommerce_app`. The role has `SELECT`-only grants on `public.*` and
`analytics.*`; `INSERT` / `UPDATE` / `DELETE` / `DDL` are denied at the
database privilege layer. This is verified by Phase 11 tests.

---

## 2. View catalog (what to import)

| View | Grain | Use for |
|---|---|---|
| `analytics.v_executive_kpis` | 1 row (whole platform) | KPI cards: total orders, GMV, cash collected, AOV, delivered %, cancelled % |
| `analytics.v_monthly_sales` | 1 row per month | Time-series line/area charts of GMV, cash, order count, AOV |
| `analytics.v_category_performance` | 1 row per `product_category_name` | Category rankings, GMV vs rating bubble, quality drill-down |
| `analytics.v_product_performance` | 1 row per `product_id` | Top / bottom N products, product review-risk analysis |
| `analytics.v_customer_performance` | 1 row per `customer_unique_id` | Individual customer profiles, top customers by GMV |
| `analytics.customer_segments` | 1 row per `customer_unique_id` (ML output) | RFM segment counts, segment-level averages |
| `analytics.v_review_analytics` | 1 row (whole platform) | Rating distribution, negative-review rate KPI |
| `analytics.v_delivery_performance` | 1 row (whole platform) | Delivery SLA KPIs |
| `analytics.v_monthly_delivery_performance` | 1 row per month | Delivery-days and late-rate trends |
| `analytics.v_order_summary` | 1 row per `order_id` (helper) | Advanced drill-down when a per-order grain is needed |

Do **not** re-derive GMV or "negative review" from `public.order_items` /
`public.order_reviews` inside DAX or a Tableau calc — use the pre-defined
column from the corresponding view. If a BI tool user needs a metric that
isn't in a view, add the column upstream in `database/analytics_schema.sql`
and rebuild; do not fork the definition into a BI file.

---

## 3. Recommended dashboards

Five focused dashboards cover the analytics surface. Each maps to a single
primary view (rarely two), so imports stay simple.

### 3.1 Executive KPI dashboard
- **Source view**: `analytics.v_executive_kpis`
- **Layout**: 5 KPI cards on top (Total Orders, Product GMV, Cash Collected, AOV, Delivered %), one card showing Cancellation %, a 2-column callout comparing GMV vs Cash Collected (they differ — see DQ-15 in `docs/data_quality_report.md`).
- **Slicer / filter**: none (this is platform-wide).

### 3.2 Sales & Revenue dashboard
- **Source view**: `analytics.v_monthly_sales` (all 25 months).
- **Charts**: dual-axis line (`product_gmv` vs `cash_collected` over `month`), bar of `order_count` by month, line of `aov_gmv` by month.
- **Note**: sales tail is truncated after 2018-08 (DQ-5). Include an annotation.

### 3.3 Category & product performance
- **Source views**: `analytics.v_category_performance`, `analytics.v_product_performance`.
- **Charts**:
  - Horizontal bar of top-N categories by `product_gmv`, colored by `avg_review_score`.
  - Scatter of categories: `product_gmv` (x) vs `avg_review_score` (y), bubble = `review_count`. Flags the "high-GMV / low-rating" quadrant for investigation.
  - Table of top-N products by `product_gmv` with columns: `product_id`, `product_category_name`, `quantity_sold`, `product_gmv`, `avg_review_score`.
- **Filter**: category multi-select.

### 3.4 Customer segmentation
- **Source view**: `analytics.customer_segments`.
- **Charts**:
  - Donut of customer counts by `segment_label`.
  - Bar of average `total_gmv` by segment.
  - Table of segment definitions (paste from README).
- **Filter**: segment multi-select.

### 3.5 Delivery & operations
- **Source views**: `analytics.v_delivery_performance` (KPIs) + `analytics.v_monthly_delivery_performance` (trend).
- **Charts**: 4 KPI cards (Delivered Orders, Avg Delivery Days, Median, Late-Delivery Rate); dual-axis line of `avg_delivery_days` vs `late_delivery_rate_pct` over month.
- **Annotation**: late deliveries are strongly associated with negative reviews (Phase 3 finding). Include as caption, phrased as association not causation.

---

## 4. Sample calculations (Power BI / DAX)

Because business logic lives in SQL, DAX stays trivial. Examples for the
Executive dashboard:

```dax
Total Orders     = SUM ( v_executive_kpis[total_orders] )
Product GMV      = SUM ( v_executive_kpis[product_gmv] )
Cash Collected   = SUM ( v_executive_kpis[cash_collected] )
Delivered Rate % = FORMAT ( AVERAGE ( v_executive_kpis[delivered_pct] ), "0.0" )
```

For Tableau, drop the same column onto the sheet with SUM / AVG aggregation.

Do **not** reimplement GMV using `SUMX ( v_order_summary[price] + v_order_summary[freight_value] )` — the definition is already baked into the view; the two forms diverge on the 249 payment-mismatch orders (DQ-15).

---

## 5. Refresh model

- **DirectQuery / live connection** works out of the box — the views are
  cheap enough for interactive querying on this dataset (~99k orders, ~112k
  items, ~104k payments).
- **Import mode** is also fine; refresh nightly or on demand.
- The `analytics.*` views are automatically up-to-date because they are
  regular Postgres views, not materialised.

---

## 6. What lives where

| Layer | Purpose | Source of truth for |
|---|---|---|
| `public.*` raw tables | immutable dataset load | row-level history |
| `analytics.*` views | curated business definitions | **all displayed metrics** |
| Streamlit dashboards | in-app analytics + AI experience | none — always reads from `analytics.*` |
| FastAPI `/api/*` | headless access | none — always reads from `analytics.*` |
| Power BI / Tableau | external professional reporting | none — always reads from `analytics.*` |

If a metric ever shows a different value across these layers, the answer is
never "the BI file is right" or "the app is right" — the answer is always
"read from the view that owns the definition." That single rule is what
keeps the platform coherent as it grows.

---

## 7. What is *not* included in this repository

- No `.pbix` (Power BI Desktop) or `.twbx` (Tableau Packaged Workbook) files
  are committed. Those are binary, tool-specific, and require the respective
  proprietary desktop apps to author. This document is the reproducible
  hand-off spec that any BI developer can follow to build them.
- If you build a `.pbix` / `.twbx` from these instructions, drop it into
  `docs/bi/` and reference it here.
