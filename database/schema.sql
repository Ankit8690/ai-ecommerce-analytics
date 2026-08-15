-- =====================================================================
-- E-Commerce BI — schema
-- =====================================================================
-- Run this inside the target database (ecommerce_ai) as ecommerce_app.
-- seed.py --bootstrap prepares the dedicated roles and schema privileges;
-- seed.py then executes this file and loads the data. This file is create-only:
-- if application tables already exist, it stops rather than replacing data.
--
-- Types, PKs, FKs and CHECKs are derived from docs/data_dictionary.md
-- and docs/database_relationships.md. Any change here must be reflected
-- in those documents.
-- =====================================================================

SET client_min_messages = WARNING;

-- ---------------------------------------------------------------------
-- geo_location — one row per zip prefix. Independent lookup table.
-- Zip <-> customers/sellers is NOT a foreign key (285 missing zips).
-- ---------------------------------------------------------------------
CREATE TABLE geo_location (
    geolocation_zip_code_prefix INTEGER       PRIMARY KEY,
    geolocation_lat             NUMERIC(10,7) NOT NULL,
    geolocation_lng             NUMERIC(10,7) NOT NULL,
    geolocation_city            TEXT          NOT NULL,
    geolocation_state           TEXT          NOT NULL
);

-- ---------------------------------------------------------------------
-- customers — one row per order-scoped customer_id.
-- The person is customer_unique_id (see data_dictionary.md).
-- ---------------------------------------------------------------------
CREATE TABLE customers (
    customer_id              VARCHAR(32) PRIMARY KEY,
    customer_unique_id       VARCHAR(32) NOT NULL,
    customer_zip_code_prefix INTEGER     NOT NULL,
    customer_city            TEXT        NOT NULL,
    customer_state           TEXT        NOT NULL
);

-- ---------------------------------------------------------------------
-- sellers
-- ---------------------------------------------------------------------
CREATE TABLE sellers (
    seller_id              VARCHAR(32) PRIMARY KEY,
    seller_zip_code_prefix INTEGER     NOT NULL,
    seller_city            TEXT,        -- 57 nulls in source
    seller_state           TEXT         -- 57 nulls in source
);

-- ---------------------------------------------------------------------
-- products — 'unknown' fills the 623 null categories on load.
-- Length/photo columns typo-corrected from source (lenght -> length).
-- ---------------------------------------------------------------------
CREATE TABLE products (
    product_id                 VARCHAR(32) PRIMARY KEY,
    product_category_name      VARCHAR(64) NOT NULL,
    product_name_length        SMALLINT,
    product_description_length INTEGER,
    product_photos_qty         SMALLINT,
    product_weight_g           INTEGER,
    product_length_cm          SMALLINT,
    product_height_cm          SMALLINT,
    product_width_cm           SMALLINT
);

-- ---------------------------------------------------------------------
-- orders
-- ---------------------------------------------------------------------
CREATE TABLE orders (
    order_id                      VARCHAR(32) PRIMARY KEY,
    customer_id                   VARCHAR(32) NOT NULL
        REFERENCES customers(customer_id),
    order_status                  VARCHAR(16) NOT NULL,
    order_purchase_timestamp      TIMESTAMP   NOT NULL,
    order_approved_at             TIMESTAMP,
    order_delivered_carrier_date  TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP   NOT NULL,
    CONSTRAINT orders_status_ck CHECK (order_status IN (
        'delivered','shipped','canceled','unavailable',
        'invoiced','processing','created','approved'
    ))
);

-- ---------------------------------------------------------------------
-- order_items — composite PK (order_id, order_item_id).
-- ---------------------------------------------------------------------
CREATE TABLE order_items (
    order_id            VARCHAR(32)   NOT NULL
        REFERENCES orders(order_id),
    order_item_id       SMALLINT      NOT NULL,
    product_id          VARCHAR(32)   NOT NULL
        REFERENCES products(product_id),
    seller_id           VARCHAR(32)   NOT NULL
        REFERENCES sellers(seller_id),
    shipping_limit_date TIMESTAMP     NOT NULL,
    price               NUMERIC(10,2) NOT NULL CHECK (price >= 0),
    freight_value       NUMERIC(10,2) NOT NULL CHECK (freight_value >= 0),
    PRIMARY KEY (order_id, order_item_id)
);

-- ---------------------------------------------------------------------
-- order_payments — composite PK (order_id, payment_sequential).
-- ---------------------------------------------------------------------
CREATE TABLE order_payments (
    order_id             VARCHAR(32)   NOT NULL
        REFERENCES orders(order_id),
    payment_sequential   SMALLINT      NOT NULL,
    payment_type         VARCHAR(16)   NOT NULL,
    payment_installments SMALLINT      NOT NULL CHECK (payment_installments >= 0),
    payment_value        NUMERIC(10,2) NOT NULL CHECK (payment_value >= 0),
    PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT payments_type_ck CHECK (payment_type IN (
        'credit_card','UPI','voucher','debit_card','not_defined'
    ))
);

-- ---------------------------------------------------------------------
-- order_reviews — deduped from ORDER_REVIEW_RATINGS.csv (see DQ-1).
-- One row per order (latest review_answer_timestamp kept).
-- ---------------------------------------------------------------------
CREATE TABLE order_reviews (
    order_id                VARCHAR(32) PRIMARY KEY
        REFERENCES orders(order_id),
    review_id               VARCHAR(32) NOT NULL,
    review_score            SMALLINT    NOT NULL
        CHECK (review_score BETWEEN 1 AND 5),
    review_creation_date    TIMESTAMP   NOT NULL,
    review_answer_timestamp TIMESTAMP   NOT NULL
);

-- ---------------------------------------------------------------------
-- Indexes (beyond automatic PK indexes). See database_relationships.md.
-- ---------------------------------------------------------------------
CREATE INDEX idx_orders_customer_id       ON orders (customer_id);
CREATE INDEX idx_orders_purchase_ts       ON orders (order_purchase_timestamp);
CREATE INDEX idx_orders_status            ON orders (order_status);

CREATE INDEX idx_order_items_product_id   ON order_items (product_id);
CREATE INDEX idx_order_items_seller_id    ON order_items (seller_id);

CREATE INDEX idx_order_payments_type      ON order_payments (payment_type);

CREATE INDEX idx_products_category        ON products (product_category_name);

CREATE INDEX idx_customers_unique_id      ON customers (customer_unique_id);
CREATE INDEX idx_customers_state          ON customers (customer_state);
CREATE INDEX idx_sellers_state            ON sellers (seller_state);

CREATE INDEX idx_reviews_score            ON order_reviews (review_score);

-- ---------------------------------------------------------------------
-- Grants — ecommerce_readonly can SELECT and nothing else. The bootstrap
-- command grants it USAGE on public; ecommerce_app owns the new tables and
-- grants access after they exist. Defaults cover future tables it creates.
-- ---------------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ecommerce_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ecommerce_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM ecommerce_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO ecommerce_readonly;
