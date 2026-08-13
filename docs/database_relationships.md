# Database Relationships

Target schema for PostgreSQL 18. All facts here are verified by
`scripts/audit_dataset.py`; the FK constraints listed below hold on the raw data
with **zero orphans**, so they can be declared safely at load time.

---

## Entity–relationship diagram

```
            ┌──────────────────────┐
            │      CUSTOMERS       │
            │  PK customer_id      │
            │     customer_unique_id  ← the person
            │     customer_zip_code_prefix ┐
            │     customer_city             │
            │     customer_state            │
            └──────────┬───────────┘        │
                       │ 1                  │
                       │                    │
                       │ N                  │
            ┌──────────▼───────────┐        │
            │        ORDERS        │        │
            │  PK order_id         │        │
            │  FK customer_id ─────┘        │
            │     order_status              │
            │     order_purchase_timestamp  │
            │     order_approved_at         │
            │     order_delivered_carrier_date
            │     order_delivered_customer_date
            │     order_estimated_delivery_date
            └───┬──────────┬────────────┬───┘
              1 │        1 │          1 │
                │          │            │
              N │        N │          1 │  (after dedup)
       ┌────────▼───┐ ┌────▼─────────┐ ┌▼────────────────────┐
       │ORDER_ITEMS │ │ORDER_PAYMENTS│ │ORDER_REVIEW_RATINGS │
       │PK order_id │ │PK order_id   │ │PK order_id          │
       │  order_item_id│  payment_seq │   review_id         │
       │FK order_id │ │FK order_id   │ │FK order_id          │
       │FK product_id──┐              │   review_score       │
       │FK seller_id───┼──┐           │   review_creation_date
       │  shipping_… │  │  │           │   review_answer_ts   │
       │  price      │  │  │           └──────────────────────┘
       │  freight    │  │  │
       └─────────────┘  │  │
                        │  │
              ┌─────────▼──┴──┐
              │   PRODUCTS    │        ┌───────────────┐
              │ PK product_id │        │   SELLERS     │
              │    category   │        │ PK seller_id  │
              │    dimensions │        │    zip prefix ─── lookup ─┐
              └───────────────┘        │    city / state           │
                                       └───────────────────────────┘
                                                                   │
                                       ┌───────────────────────────▼┐
                                       │       GEO_LOCATION         │
                                       │  PK geolocation_zip_prefix │
                                       │     lat / lng              │
                                       │     city / state           │
                                       └────────────────────────────┘
```

CUSTOMERS.zip and SELLERS.zip are **lookup joins** to GEO_LOCATION — they are
not enforced as FKs because 278 customer zips and 7 seller zips have no
GEO_LOCATION row (DQ-6). All other relationships are true foreign keys.

---

## Primary keys

| Table | Primary key |
|---|---|
| CUSTOMERS | `customer_id` |
| GEO_LOCATION | `geolocation_zip_code_prefix` |
| ORDERS | `order_id` |
| ORDER_ITEMS | (`order_id`, `order_item_id`) |
| ORDER_PAYMENTS | (`order_id`, `payment_sequential`) |
| ORDER_REVIEW_RATINGS | `order_id` *(after Phase 1 dedup — see DQ-1)* |
| PRODUCTS | `product_id` |
| SELLERS | `seller_id` |

---

## Foreign keys (enforced)

Format: `child.column → parent.column · verified orphan count`.

| # | Constraint | Orphans |
|---|---|---:|
| FK1 | `orders.customer_id → customers.customer_id` | 0 |
| FK2 | `order_items.order_id → orders.order_id` | 0 |
| FK3 | `order_items.product_id → products.product_id` | 0 |
| FK4 | `order_items.seller_id → sellers.seller_id` | 0 |
| FK5 | `order_payments.order_id → orders.order_id` | 0 |
| FK6 | `order_reviews.order_id → orders.order_id` | 0 |

Cascade policy: `ON UPDATE NO ACTION ON DELETE NO ACTION`. The dataset is a
snapshot; nothing is meant to change under the schema.

## Lookup joins (not enforced)

| Join | Reason not enforced |
|---|---|
| `customers.customer_zip_code_prefix ↔ geo_location.geolocation_zip_code_prefix` | 278 (0.28%) missing |
| `sellers.seller_zip_code_prefix ↔ geo_location.geolocation_zip_code_prefix` | 7 (0.23%) missing |

Use `LEFT JOIN` at query time.

---

## Cardinalities and join semantics

| Parent | Child | Cardinality | Coverage |
|---|---|---|---|
| CUSTOMERS | ORDERS | 1 → 1..N | Every order has exactly one customer_id. 96,096 unique persons behind 99,441 orders (via `customer_unique_id`). |
| ORDERS | ORDER_ITEMS | 1 → 0..N | 98,666 / 99,441 orders have ≥1 item; 775 have none (DQ-3). Line count ranges 1..21. |
| ORDERS | ORDER_PAYMENTS | 1 → 0..N | 99,440 / 99,441 have ≥1 payment; 1 has none. Sequential count ranges 1..29. |
| ORDERS | ORDER_REVIEW_RATINGS | 1 → 1..M in raw file, **1 → 1 after dedup** | Raw file has 1..M with the padded rows; loader collapses to one row per order (DQ-1). |
| PRODUCTS | ORDER_ITEMS | 1 → 0..N | Every product in the file is sold at least once. |
| SELLERS | ORDER_ITEMS | 1 → 0..N | Every seller is active. |

---

## Canonical join recipes

Recipes downstream code should use. Written once here so every layer agrees.

**Revenue per order** (GMV, includes freight):
```sql
SELECT o.order_id,
       SUM(oi.price)          AS item_total,
       SUM(oi.freight_value)  AS freight_total,
       SUM(oi.price + oi.freight_value) AS gmv
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
GROUP BY o.order_id;
```

**Cash received per order** (may differ slightly from GMV — DQ-15):
```sql
SELECT o.order_id,
       SUM(p.payment_value) AS paid
FROM orders o
JOIN order_payments p ON p.order_id = o.order_id
GROUP BY o.order_id;
```

**One row per person** (RFM building block — DQ-10):
```sql
SELECT c.customer_unique_id,
       COUNT(DISTINCT o.order_id)              AS orders,
       MAX(o.order_purchase_timestamp)         AS last_order_ts,
       MIN(o.order_purchase_timestamp)         AS first_order_ts
FROM customers c
JOIN orders o USING (customer_id)
GROUP BY c.customer_unique_id;
```

**Delivery performance** (excludes non-delivered and DQ-9 rows):
```sql
SELECT order_id,
       EXTRACT(EPOCH FROM (order_delivered_customer_date - order_purchase_timestamp))/86400
         AS days_to_deliver,
       EXTRACT(EPOCH FROM (order_delivered_customer_date - order_estimated_delivery_date))/86400
         AS delay_vs_estimate
FROM orders
WHERE order_status = 'delivered'
  AND order_delivered_customer_date IS NOT NULL;
```

**Category performance** (uses the dedup `unknown` category from DQ-7):
```sql
SELECT p.product_category_name,
       COUNT(DISTINCT oi.order_id)  AS orders,
       SUM(oi.price)                AS revenue
FROM order_items oi
JOIN products p USING (product_id)
GROUP BY p.product_category_name
ORDER BY revenue DESC;
```

---

## Load order (Phase 1)

Because of FK constraints, load parents before children:

1. `geo_location`, `products`, `sellers`, `customers` (independent)
2. `orders` (needs customers)
3. `order_items` (needs orders, products, sellers)
4. `order_payments` (needs orders)
5. `order_reviews` (needs orders; deduplicated during transform, see DQ-1)

---

## Indexes to create at load time

Beyond the automatic PK indexes, Phase 1 adds:

- `orders (customer_id)` — for RFM joins
- `orders (order_purchase_timestamp)` — for time-series analytics
- `orders (order_status)` — for filtered aggregates
- `order_items (product_id)` — for product analytics
- `order_items (seller_id)` — for seller analytics
- `order_payments (payment_type)` — for payment-mix breakdowns
- `products (product_category_name)` — for category rollups

No indexes on `customer_unique_id` (only used in dedicated CTEs, not row-lookup).
