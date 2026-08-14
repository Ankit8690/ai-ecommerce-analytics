-- =====================================================================
-- E-Commerce Business Intelligence — Analytics Layer Schema & Views
-- =====================================================================
-- Schema: analytics
-- Description: Reusable PostgreSQL analytics views built on top of the
-- core ecommerce_ai schema (public).
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS analytics;

-- Grants for dedicated roles
GRANT USAGE ON SCHEMA analytics TO ecommerce_app;
GRANT USAGE ON SCHEMA analytics TO ecommerce_readonly;

-- ---------------------------------------------------------------------
-- 1. Helper View: v_order_summary
-- Pre-aggregates order_items and order_payments to the order level.
-- Solves 3-way join multiplication and handles 775 item-less orders.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_order_summary AS
WITH items_agg AS (
    SELECT
        order_id,
        COUNT(order_item_id)                AS item_count,
        SUM(price)                          AS item_price_total,
        SUM(freight_value)                  AS freight_total,
        SUM(price + freight_value)          AS product_gmv
    FROM public.order_items
    GROUP BY order_id
),
payments_agg AS (
    SELECT
        order_id,
        COUNT(payment_sequential)           AS payment_count,
        SUM(payment_value)                  AS cash_collected
    FROM public.order_payments
    GROUP BY order_id
)
SELECT
    o.order_id,
    o.customer_id,
    c.customer_unique_id,
    c.customer_city,
    c.customer_state,
    o.order_status,
    o.order_purchase_timestamp,
    o.order_approved_at,
    o.order_delivered_carrier_date,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    COALESCE(i.item_count, 0)               AS item_count,
    COALESCE(i.item_price_total, 0.00)      AS item_price_total,
    COALESCE(i.freight_total, 0.00)         AS freight_total,
    COALESCE(i.product_gmv, 0.00)           AS product_gmv,
    COALESCE(p.payment_count, 0)            AS payment_count,
    COALESCE(p.cash_collected, 0.00)        AS cash_collected,
    r.review_score,
    CASE WHEN r.review_score IS NOT NULL AND r.review_score <= 2 THEN 1 ELSE 0 END AS is_negative_review
FROM public.orders o
JOIN public.customers c ON c.customer_id = o.customer_id
LEFT JOIN items_agg i ON i.order_id = o.order_id
LEFT JOIN payments_agg p ON p.order_id = o.order_id
LEFT JOIN public.order_reviews r ON r.order_id = o.order_id;


-- ---------------------------------------------------------------------
-- 2. View: v_executive_kpis
-- High-level business performance metrics & revenue breakdown
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_executive_kpis AS
WITH base AS (
    SELECT
        COUNT(order_id)                                                AS total_orders,
        COUNT(DISTINCT customer_id)                                    AS total_customers,
        COUNT(DISTINCT customer_unique_id)                             AS total_unique_customers,
        SUM(product_gmv)                                               AS product_gmv,
        SUM(cash_collected)                                            AS cash_collected,
        ROUND(AVG(product_gmv)::numeric, 2)                            AS avg_order_value_gmv,
        ROUND(AVG(cash_collected)::numeric, 2)                         AS avg_order_value_cash,
        COUNT(CASE WHEN order_status = 'delivered' THEN 1 END)          AS delivered_orders,
        COUNT(CASE WHEN order_status = 'canceled' THEN 1 END)           AS cancelled_orders,
        COUNT(CASE WHEN order_status = 'unavailable' THEN 1 END)        AS unavailable_orders,
        COUNT(CASE WHEN order_status = 'shipped' THEN 1 END)            AS shipped_orders,
        COUNT(CASE WHEN order_status = 'invoiced' THEN 1 END)           AS invoiced_orders,
        COUNT(CASE WHEN order_status = 'processing' THEN 1 END)         AS processing_orders,
        COUNT(CASE WHEN order_status = 'created' THEN 1 END)            AS created_orders,
        COUNT(CASE WHEN order_status = 'approved' THEN 1 END)           AS approved_orders
    FROM analytics.v_order_summary
),
counts AS (
    SELECT
        (SELECT COUNT(*) FROM public.products)                         AS total_products,
        (SELECT COUNT(*) FROM public.sellers)                          AS total_sellers
)
SELECT
    b.total_orders,
    b.total_customers,
    b.total_unique_customers,
    c.total_products,
    c.total_sellers,
    b.product_gmv,
    b.cash_collected,
    b.avg_order_value_gmv,
    b.avg_order_value_cash,
    b.delivered_orders,
    ROUND(100.0 * b.delivered_orders / NULLIF(b.total_orders, 0), 2)   AS delivered_pct,
    b.cancelled_orders,
    ROUND(100.0 * b.cancelled_orders / NULLIF(b.total_orders, 0), 2)   AS cancelled_pct,
    b.unavailable_orders,
    b.shipped_orders,
    b.invoiced_orders,
    b.processing_orders,
    b.created_orders,
    b.approved_orders
FROM base b, counts c;


-- ---------------------------------------------------------------------
-- 3. View: v_monthly_sales
-- Monthly aggregation of GMV, cash collected, orders, and customers
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_monthly_sales AS
SELECT
    DATE_TRUNC('month', order_purchase_timestamp)::date               AS month,
    COUNT(order_id)                                                   AS order_count,
    COUNT(DISTINCT customer_unique_id)                                AS unique_customers,
    SUM(product_gmv)                                                  AS product_gmv,
    SUM(cash_collected)                                               AS cash_collected,
    ROUND(SUM(product_gmv) / NULLIF(COUNT(order_id), 0), 2)           AS aov_gmv,
    ROUND(SUM(cash_collected) / NULLIF(COUNT(order_id), 0), 2)        AS aov_cash
FROM analytics.v_order_summary
GROUP BY 1
ORDER BY 1;


-- ---------------------------------------------------------------------
-- 4. View: v_category_performance
-- Category-level volume, revenue, item pricing, and review ratings
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_category_performance AS
SELECT
    p.product_category_name,
    COUNT(DISTINCT oi.order_id)                                       AS order_count,
    COUNT(oi.order_item_id)                                           AS quantity_sold,
    SUM(oi.price + oi.freight_value)                                  AS product_gmv,
    SUM(oi.price)                                                     AS item_revenue,
    SUM(oi.freight_value)                                             AS freight_total,
    ROUND(AVG(oi.price)::numeric, 2)                                  AS avg_item_price,
    ROUND(AVG(oi.freight_value)::numeric, 2)                          AS avg_freight_value,
    COUNT(r.review_score)                                             AS review_count,
    ROUND(AVG(r.review_score)::numeric, 2)                            AS avg_review_score,
    COUNT(CASE WHEN r.review_score <= 2 THEN 1 END)                   AS negative_review_count,
    ROUND(100.0 * COUNT(CASE WHEN r.review_score <= 2 THEN 1 END) / 
          NULLIF(COUNT(r.review_score), 0), 2)                        AS negative_review_rate_pct,
    DENSE_RANK() OVER (ORDER BY SUM(oi.price + oi.freight_value) DESC NULLS LAST) AS gmv_rank
FROM public.products p
LEFT JOIN public.order_items oi ON oi.product_id = p.product_id
LEFT JOIN public.order_reviews r ON r.order_id = oi.order_id
GROUP BY p.product_category_name
ORDER BY product_gmv DESC NULLS LAST;


-- ---------------------------------------------------------------------
-- 5. View: v_product_performance
-- Product-level sales, revenue, reviews, and rankings
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_product_performance AS
SELECT
    p.product_id,
    p.product_category_name,
    COALESCE(COUNT(oi.order_item_id), 0)                              AS quantity_sold,
    COALESCE(COUNT(DISTINCT oi.order_id), 0)                          AS order_count,
    COALESCE(SUM(oi.price + oi.freight_value), 0.00)                 AS product_gmv,
    COALESCE(SUM(oi.price), 0.00)                                     AS item_revenue,
    COALESCE(SUM(oi.freight_value), 0.00)                             AS freight_total,
    ROUND(AVG(oi.price)::numeric, 2)                                  AS avg_item_price,
    ROUND(AVG(oi.freight_value)::numeric, 2)                          AS avg_freight_value,
    COUNT(r.review_score)                                             AS review_count,
    ROUND(AVG(r.review_score)::numeric, 2)                            AS avg_review_score,
    COUNT(CASE WHEN r.review_score <= 2 THEN 1 END)                   AS negative_review_count,
    DENSE_RANK() OVER (ORDER BY COALESCE(SUM(oi.price + oi.freight_value), 0) DESC) AS gmv_rank
FROM public.products p
LEFT JOIN public.order_items oi ON oi.product_id = p.product_id
LEFT JOIN public.order_reviews r ON r.order_id = oi.order_id
GROUP BY p.product_id, p.product_category_name;


-- ---------------------------------------------------------------------
-- 6. View: v_customer_performance
-- Customer-level aggregation (by customer_unique_id)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_customer_performance AS
SELECT
    c.customer_unique_id,
    COUNT(DISTINCT o.order_id)                                        AS order_count,
    SUM(s.product_gmv)                                                AS total_gmv,
    SUM(s.cash_collected)                                             AS total_cash_collected,
    ROUND(SUM(s.product_gmv) / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) AS avg_order_value_gmv,
    MIN(o.order_purchase_timestamp)                                   AS first_order_date,
    MAX(o.order_purchase_timestamp)                                   AS latest_order_date,
    CASE WHEN COUNT(DISTINCT o.order_id) > 1 THEN TRUE ELSE FALSE END AS is_repeat_customer
FROM public.customers c
JOIN public.orders o ON o.customer_id = c.customer_id
JOIN analytics.v_order_summary s ON s.order_id = o.order_id
GROUP BY c.customer_unique_id;


-- ---------------------------------------------------------------------
-- 7. View: v_review_analytics
-- Customer experience and review score distribution metrics
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_review_analytics AS
SELECT
    COUNT(*)                                                          AS total_reviews,
    ROUND(AVG(review_score)::numeric, 2)                              AS avg_review_score,
    COUNT(CASE WHEN review_score = 1 THEN 1 END)                      AS rating_1_count,
    COUNT(CASE WHEN review_score = 2 THEN 1 END)                      AS rating_2_count,
    COUNT(CASE WHEN review_score = 3 THEN 1 END)                      AS rating_3_count,
    COUNT(CASE WHEN review_score = 4 THEN 1 END)                      AS rating_4_count,
    COUNT(CASE WHEN review_score = 5 THEN 1 END)                      AS rating_5_count,
    COUNT(CASE WHEN review_score <= 2 THEN 1 END)                     AS negative_review_count,
    ROUND(100.0 * COUNT(CASE WHEN review_score <= 2 THEN 1 END) / 
          NULLIF(COUNT(*), 0), 2)                                     AS negative_review_rate_pct
FROM public.order_reviews;


-- ---------------------------------------------------------------------
-- 8. View: v_delivery_performance
-- Overall operational and delivery SLA performance
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_delivery_performance AS
WITH delivery_metrics AS (
    SELECT
        order_id,
        EXTRACT(EPOCH FROM (order_delivered_customer_date - order_purchase_timestamp))/86400.0 AS delivery_days,
        CASE WHEN order_delivered_customer_date > order_estimated_delivery_date THEN 1 ELSE 0 END AS is_late
    FROM public.orders
    WHERE order_status = 'delivered'
      AND order_delivered_customer_date IS NOT NULL
)
SELECT
    COUNT(*)                                                          AS delivered_order_count,
    ROUND(AVG(delivery_days)::numeric, 2)                             AS avg_delivery_days,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delivery_days)::numeric, 2) AS median_delivery_days,
    SUM(is_late)                                                      AS late_delivery_count,
    ROUND(100.0 * SUM(is_late) / NULLIF(COUNT(*), 0), 2)               AS late_delivery_rate_pct,
    ROUND(100.0 - (100.0 * SUM(is_late) / NULLIF(COUNT(*), 0)), 2)     AS on_time_rate_pct
FROM delivery_metrics;


-- ---------------------------------------------------------------------
-- 9. View: v_monthly_delivery_performance
-- Time-series monthly operational and delivery performance
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_monthly_delivery_performance AS
WITH delivery_metrics AS (
    SELECT
        DATE_TRUNC('month', order_purchase_timestamp)::date            AS month,
        EXTRACT(EPOCH FROM (order_delivered_customer_date - order_purchase_timestamp))/86400.0 AS delivery_days,
        CASE WHEN order_delivered_customer_date > order_estimated_delivery_date THEN 1 ELSE 0 END AS is_late
    FROM public.orders
    WHERE order_status = 'delivered'
      AND order_delivered_customer_date IS NOT NULL
)
SELECT
    month,
    COUNT(*)                                                          AS delivered_order_count,
    ROUND(AVG(delivery_days)::numeric, 2)                             AS avg_delivery_days,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delivery_days)::numeric, 2) AS median_delivery_days,
    SUM(is_late)                                                      AS late_delivery_count,
    ROUND(100.0 * SUM(is_late) / NULLIF(COUNT(*), 0), 2)               AS late_delivery_rate_pct,
    ROUND(100.0 - (100.0 * SUM(is_late) / NULLIF(COUNT(*), 0)), 2)     AS on_time_rate_pct
FROM delivery_metrics
GROUP BY month
ORDER BY month;


-- Grants on created views in analytics schema
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO ecommerce_app;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO ecommerce_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO ecommerce_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO ecommerce_app;
