"""
Reproducible database initialisation and data-loading.

What this script does, in order:

  1. `--bootstrap` reads the administrator settings and creates/updates only
     the two dedicated application roles, then grants them the least access
     needed on the already-created ecommerce_ai database.
  2. The normal command connects only with DATABASE_URL (ecommerce_app) and:
     - executes database/schema.sql (create-only; never drops tables),
     - loads the 8 CSVs from DATASET_RAW_DIR with cleaning:
         * timestamps parsed from 'M/D/YYYY H:MM' text,
         * two typo columns in PRODUCTS renamed,
         * missing product_category_name filled with 'unknown',
         * order_reviews deduplicated to one row per order_id.
     - runs verification (row counts, FK integrity, representative
       queries, read-only role denial).

Usage:
    python database/seed.py --bootstrap  # prepare roles and permissions only
    python database/seed.py              # initialize empty schema and load all data
    python database/seed.py --tables order_payments  # safely resume one empty table
    python database/seed.py --verify     # verify only, do not reload
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
TARGET_DATABASE = "ecommerce_ai"
APP_ROLE = "ecommerce_app"
READONLY_ROLE = "ecommerce_readonly"


# ─────────────────────────────────────────────────────────────────────
# Env / config
# ─────────────────────────────────────────────────────────────────────
def load_env(*, for_bootstrap: bool = False) -> dict[str, str]:
    load_dotenv(ROOT / ".env")
    required = [
        "DATABASE_URL",
        "DATABASE_URL_READONLY",
        "DATASET_RAW_DIR",
    ]
    if for_bootstrap:
        required += [
            "DATABASE_ADMIN_URL",
            "APP_DB_PASSWORD",
            "READONLY_DB_PASSWORD",
        ]
    cfg = {k: os.environ.get(k, "") for k in required}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        sys.exit(f"Missing required .env keys: {missing}. Copy .env.example -> .env and fill them.")
    # Managed cloud providers hand out plain `postgresql://` URLs; SQLAlchemy needs
    # the driver hint. Normalise in-place so callers can paste the URL verbatim.
    for k in ("DATABASE_URL", "DATABASE_URL_READONLY", "DATABASE_ADMIN_URL"):
        v = cfg.get(k, "")
        if v.startswith("postgresql://"):
            cfg[k] = v.replace("postgresql://", "postgresql+psycopg://", 1)
    return cfg


def validate_connection_roles(cfg: dict[str, str], *, require_admin: bool = False) -> None:
    """Fail before any database work if URLs do not match the fixed role model.

    The local-only role/database name checks catch accidental targeting of the
    wrong DB during development. Managed cloud providers (Render, Railway,
    Neon, RDS, …) auto-name databases and expose only one role, so these
    checks would block legitimate cloud seeding. Setting the environment
    variable ``SEED_ALLOW_REMOTE=1`` bypasses just the name/role checks
    (never the presence checks). Use only when targeting a managed service.
    """
    app_url = make_url(cfg["DATABASE_URL"])
    readonly_url = make_url(cfg["DATABASE_URL_READONLY"])
    problems: list[str] = []
    allow_remote = os.getenv("SEED_ALLOW_REMOTE") == "1"

    if not allow_remote:
        if app_url.database != TARGET_DATABASE:
            problems.append(f"DATABASE_URL must target {TARGET_DATABASE!r}")
        if app_url.username != APP_ROLE:
            problems.append(f"DATABASE_URL must use the dedicated {APP_ROLE!r} role, never postgres")
        if readonly_url.database != TARGET_DATABASE:
            problems.append(f"DATABASE_URL_READONLY must target {TARGET_DATABASE!r}")
        if readonly_url.username != READONLY_ROLE:
            problems.append(f"DATABASE_URL_READONLY must use {READONLY_ROLE!r}")

    if require_admin:
        admin_url = make_url(cfg["DATABASE_ADMIN_URL"])
        if not allow_remote:
            if admin_url.database == TARGET_DATABASE:
                problems.append("DATABASE_ADMIN_URL must connect to an administrative database, not ecommerce_ai")
            if admin_url.username in {APP_ROLE, READONLY_ROLE}:
                problems.append("DATABASE_ADMIN_URL must use an administrator role")

    if problems:
        hint = ("" if allow_remote else
                "\nHint: for managed cloud databases (Render/Railway/RDS/…) set "
                "SEED_ALLOW_REMOTE=1 to bypass local-only role checks.")
        sys.exit("Invalid database configuration: " + "; ".join(problems) + hint)


# ─────────────────────────────────────────────────────────────────────
# 1. Bootstrap: roles + database
# ─────────────────────────────────────────────────────────────────────
def bootstrap(cfg: dict[str, str]) -> None:
    """Prepare dedicated roles for an already-created ecommerce_ai database."""
    admin_url = make_url(cfg["DATABASE_ADMIN_URL"])
    target_db = TARGET_DATABASE

    engine = create_engine(admin_url)
    with engine.begin() as conn:
        print(f"[bootstrap] admin connection: {admin_url.render_as_string(hide_password=True)}")

        # The database is user-created and must already exist. Never create it.
        db_exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :d"),
            {"d": target_db},
        ).scalar()
        if not db_exists:
            sys.exit(f"[bootstrap] required database '{target_db}' does not exist; create it manually first")

        # Only the two dedicated roles may be created or have credentials
        # rotated here. In particular, postgres is never altered.
        for user, pwd in [(APP_ROLE, cfg["APP_DB_PASSWORD"]),
                          (READONLY_ROLE, cfg["READONLY_DB_PASSWORD"])]:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :u"),
                {"u": user},
            ).scalar()
            if exists:
                statement_template = "ALTER ROLE %I WITH LOGIN PASSWORD %L"
            else:
                statement_template = "CREATE ROLE %I WITH LOGIN PASSWORD %L"
            # PostgreSQL does not accept bind parameters in CREATE/ALTER ROLE.
            # Its format() function quotes the fixed role name and password on
            # the server, avoiding unsafe Python-side string interpolation.
            try:
                statement = conn.execute(
                    text("SELECT format(CAST(:template AS text), CAST(:role_name AS text), CAST(:password AS text))"),
                    {"template": statement_template, "role_name": user, "password": pwd},
                ).scalar_one()
                conn.exec_driver_sql(statement)
            except SQLAlchemyError:
                raise RuntimeError("[bootstrap] dedicated role configuration failed; credential details are suppressed") from None
            print(f"[bootstrap] role '{user}' {'password refreshed' if exists else 'created'}")

        # The app role may connect to the target database. It receives no
        # database ownership or superuser privileges.
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{target_db}" TO "{APP_ROLE}"'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{target_db}" TO "{READONLY_ROLE}"'))

    engine.dispose()

    # Schema privileges must be granted while connected to ecommerce_ai; the
    # administrative URL normally points at postgres instead.
    target_engine = create_engine(admin_url.set(database=target_db))
    with target_engine.begin() as conn:
        conn.execute(text(f'GRANT USAGE, CREATE ON SCHEMA public TO "{APP_ROLE}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{READONLY_ROLE}"'))
    target_engine.dispose()
    print(f"[bootstrap] granted application and read-only access to '{target_db}'")


# ─────────────────────────────────────────────────────────────────────
# 2. Schema
# ─────────────────────────────────────────────────────────────────────
def apply_schema(engine: Engine) -> None:
    sql = SCHEMA_SQL.read_text(encoding="utf-8")

    # On managed cloud DBs the readonly role isn't pre-created by our bootstrap
    # step (SEED_ALLOW_REMOTE=1 skipped bootstrap). schema.sql then errors on
    # GRANT/REVOKE lines that reference `ecommerce_readonly`. Ensure the role
    # exists first — idempotent; requires the current role to have CREATEROLE
    # (Render's primary user has it).
    if os.getenv("SEED_ALLOW_REMOTE") == "1":
        readonly_pw = os.getenv("READONLY_DB_PASSWORD") or "readonly_placeholder_pw"
        ensure_role_sql = f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READONLY_ROLE}') THEN
                CREATE ROLE {READONLY_ROLE} WITH LOGIN PASSWORD '{readonly_pw}';
            END IF;
        END $$;
        """
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(ensure_role_sql)
            print(f"[schema] ensured role '{READONLY_ROLE}' exists on remote")
        except Exception as e:
            print(f"[schema] WARNING: could not ensure {READONLY_ROLE!r}: {e}")
            print("[schema] continuing; schema.sql GRANTs may fail if role is missing")

    with engine.begin() as conn:
        # SQLAlchemy's exec_driver_sql sends the whole batch verbatim, which
        # is what schema.sql expects.
        conn.exec_driver_sql(sql)
    print(f"[schema] applied {SCHEMA_SQL.name}")


# ─────────────────────────────────────────────────────────────────────
# 3. CSV loaders
# ─────────────────────────────────────────────────────────────────────
DATE_FMT = "%m/%d/%Y %H:%M"
LOAD_ORDER = [
    "geo_location", "customers", "sellers", "products",
    "orders", "order_items", "order_payments", "order_reviews",
]


def _to_ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format=DATE_FMT, errors="coerce")


def _read(raw_dir: Path, name: str) -> pd.DataFrame:
    path = raw_dir / f"{name}.csv"
    if not path.exists():
        sys.exit(f"Missing dataset file: {path}")
    return pd.read_csv(path, low_memory=False)


def prepare_frames(raw_dir: Path, tables: set[str] | None = None) -> dict[str, pd.DataFrame]:
    """Read and clean only the requested source CSVs, ready for loading."""
    requested = set(LOAD_ORDER if tables is None else tables)
    unknown = requested.difference(LOAD_ORDER)
    if unknown:
        raise ValueError(f"Unknown target tables: {sorted(unknown)}")
    print(f"[load] reading source data for: {', '.join(name for name in LOAD_ORDER if name in requested)}")

    frames: dict[str, pd.DataFrame] = {}
    if "geo_location" in requested:
        frames["geo_location"] = _read(raw_dir, "GEO_LOCATION")
    if "customers" in requested:
        frames["customers"] = _read(raw_dir, "CUSTOMERS")
    if "sellers" in requested:
        frames["sellers"] = _read(raw_dir, "SELLERS")
    if "products" in requested:
        products = _read(raw_dir, "PRODUCTS").rename(columns={
            "product_name_lenght": "product_name_length",
            "product_description_lenght": "product_description_length",
        })
        frames["products"] = products.assign(
            product_category_name=products["product_category_name"].fillna("unknown")
        )
    if "orders" in requested:
        orders = _read(raw_dir, "ORDERS")
        for c in [
            "order_purchase_timestamp", "order_approved_at",
            "order_delivered_carrier_date", "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ]:
            orders[c] = _to_ts(orders[c])
        frames["orders"] = orders
    if "order_items" in requested:
        order_items = _read(raw_dir, "ORDER_ITEMS")
        order_items["shipping_limit_date"] = _to_ts(order_items["shipping_limit_date"])
        frames["order_items"] = order_items
    if "order_payments" in requested:
        frames["order_payments"] = _read(raw_dir, "ORDER_PAYMENTS")
    if "order_reviews" in requested:
        # Keep the audited deduplication rule: latest answer timestamp per order.
        rv = _read(raw_dir, "ORDER_REVIEW_RATINGS")
        rv["review_creation_date"] = _to_ts(rv["review_creation_date"])
        rv["review_answer_timestamp"] = _to_ts(rv["review_answer_timestamp"])
        rv = rv.sort_values(
            ["order_id", "review_answer_timestamp", "review_creation_date", "review_id"]
        ).drop_duplicates(subset=["order_id"], keep="last").reset_index(drop=True)
        frames["order_reviews"] = rv[[
            "order_id", "review_id", "review_score",
            "review_creation_date", "review_answer_timestamp",
        ]]
    return frames


def load_frames(engine: Engine, frames: dict[str, pd.DataFrame]) -> None:
    """Load each frame in FK-safe order using pandas.to_sql with COPY-ish speed."""
    for name in LOAD_ORDER:
        if name not in frames:
            continue
        df = frames[name]
        print(f"[load] {name:<15} {len(df):>7,} rows ...", end=" ", flush=True)
        df.to_sql(
            name,
            engine,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=2000,
        )
        print("done")


# ─────────────────────────────────────────────────────────────────────
# 4. Verification
# ─────────────────────────────────────────────────────────────────────
EXPECTED_ROWS = {
    "geo_location":   19_015,
    "customers":      99_441,
    "sellers":         3_095,
    "products":       32_951,
    "orders":         99_441,
    "order_items":   112_650,
    "order_payments": 103_886,
    "order_reviews":  99_441,   # after dedup (raw file has 100,000)
}


def validate_resume_state(engine: Engine, targets: set[str]) -> None:
    """Refuse a resume unless targets are empty and their FK parents are complete."""
    expected_tables = set(EXPECTED_ROWS)
    dependencies = {
        "orders": {"customers"},
        "order_items": {"orders", "products", "sellers"},
        "order_payments": {"orders"},
        "order_reviews": {"orders"},
    }
    with engine.connect() as conn:
        actual_tables = set(conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )).scalars())
        if actual_tables != expected_tables:
            sys.exit("[resume] public schema does not match the audited eight-table model")
        for table, expected in EXPECTED_ROWS.items():
            got = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            if table in targets and got != 0:
                sys.exit(f"[resume] refusing to append: target table {table!r} already has {got:,} rows")
            if table not in targets and got not in {0, expected}:
                sys.exit(
                    f"[resume] refusing to continue: existing table {table!r} has {got:,} rows; "
                    f"expected either 0 or {expected:,}"
                )
        for table in targets:
            for parent in dependencies.get(table, set()):
                got = conn.execute(text(f"SELECT COUNT(*) FROM {parent}")).scalar_one()
                if got != EXPECTED_ROWS[parent]:
                    sys.exit(
                        f"[resume] refusing to load {table!r}: required parent {parent!r} has "
                        f"{got:,} rows; expected {EXPECTED_ROWS[parent]:,}"
                    )
    print(f"[resume] preflight passed; only {', '.join(sorted(targets))} will be loaded")


def verify_targets(engine: Engine, targets: set[str]) -> None:
    """Verify resumed target counts and their audited foreign keys without writes."""
    fk_checks = {
        "order_payments": "SELECT COUNT(*) FROM order_payments p LEFT JOIN orders o USING(order_id) WHERE o.order_id IS NULL",
        "order_reviews": "SELECT COUNT(*) FROM order_reviews r LEFT JOIN orders o USING(order_id) WHERE o.order_id IS NULL",
    }
    with engine.connect() as conn:
        for table in LOAD_ORDER:
            if table not in targets:
                continue
            got = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            expected = EXPECTED_ROWS[table]
            print(f"[resume] {table}: got={got:,} expected={expected:,}")
            if got != expected:
                sys.exit(f"[resume] row-count mismatch for {table}")
            if table in fk_checks:
                orphans = conn.execute(text(fk_checks[table])).scalar_one()
                print(f"[resume] {table}: foreign-key orphans={orphans}")
                if orphans:
                    sys.exit(f"[resume] foreign-key violation for {table}")


def verify(engine: Engine, ro_engine: Engine) -> None:
    print("\n[verify] ─────────────────────────────────────────────────────────")

    with engine.connect() as conn:
        # Row counts
        print("[verify] row counts:")
        problems = 0
        for tbl, expected in EXPECTED_ROWS.items():
            got = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar_one()
            ok = got == expected
            print(f"   {tbl:<15} got={got:>7,}  expected={expected:>7,}  {'OK' if ok else 'MISMATCH'}")
            problems += (0 if ok else 1)
        if problems:
            sys.exit(f"[verify] {problems} row-count mismatches")

        # FK integrity (enforced by the schema; a bad load would have failed).
        # We still assert zero-orphan explicitly, as a load-independent check.
        print("\n[verify] FK integrity (orphans should all be 0):")
        fks = [
            ("orders.customer_id -> customers",
             "SELECT COUNT(*) FROM orders o LEFT JOIN customers c USING(customer_id) WHERE c.customer_id IS NULL"),
            ("order_items.order_id -> orders",
             "SELECT COUNT(*) FROM order_items i LEFT JOIN orders o USING(order_id) WHERE o.order_id IS NULL"),
            ("order_items.product_id -> products",
             "SELECT COUNT(*) FROM order_items i LEFT JOIN products p USING(product_id) WHERE p.product_id IS NULL"),
            ("order_items.seller_id -> sellers",
             "SELECT COUNT(*) FROM order_items i LEFT JOIN sellers s USING(seller_id) WHERE s.seller_id IS NULL"),
            ("order_payments.order_id -> orders",
             "SELECT COUNT(*) FROM order_payments p LEFT JOIN orders o USING(order_id) WHERE o.order_id IS NULL"),
            ("order_reviews.order_id -> orders",
             "SELECT COUNT(*) FROM order_reviews r LEFT JOIN orders o USING(order_id) WHERE o.order_id IS NULL"),
        ]
        for label, sql in fks:
            n = conn.execute(text(sql)).scalar_one()
            print(f"   {label:<40} orphans={n}")
            if n != 0:
                sys.exit(f"[verify] FK orphans detected: {label}")

        # Indexes present (spot-check)
        print("\n[verify] indexes present:")
        want = {
            "idx_orders_customer_id", "idx_orders_purchase_ts", "idx_orders_status",
            "idx_order_items_product_id", "idx_order_items_seller_id",
            "idx_order_payments_type", "idx_products_category",
            "idx_customers_unique_id", "idx_reviews_score",
        }
        rows = conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
        )).scalars().all()
        have = set(rows)
        for ix in sorted(want):
            print(f"   {ix:<32} {'OK' if ix in have else 'MISSING'}")
            if ix not in have:
                sys.exit(f"[verify] missing index: {ix}")

        # Representative analytical queries
        print("\n[verify] representative queries:")

        top_categories = conn.execute(text("""
            SELECT p.product_category_name, ROUND(SUM(oi.price)::numeric, 2) AS revenue
            FROM order_items oi
            JOIN products p USING (product_id)
            GROUP BY p.product_category_name
            ORDER BY revenue DESC
            LIMIT 5
        """)).all()
        print("   Top 5 categories by revenue:")
        for cat, rev in top_categories:
            print(f"     {cat:<28} {rev:>12,.2f}")

        pm = conn.execute(text("""
            SELECT DATE_TRUNC('month', order_purchase_timestamp)::date AS month,
                   COUNT(*)                                            AS orders
            FROM orders
            WHERE order_purchase_timestamp >= DATE '2018-05-01'
              AND order_purchase_timestamp <  DATE '2018-09-01'
            GROUP BY 1 ORDER BY 1
        """)).all()
        print("   Orders per month, May–Aug 2018:")
        for month, n in pm:
            print(f"     {month}   {n:>6,}")

        delivery = conn.execute(text("""
            SELECT COUNT(*)                                            AS delivered,
                   ROUND(AVG(EXTRACT(EPOCH FROM
                    (order_delivered_customer_date - order_purchase_timestamp))/86400)::numeric, 2)
                                                                       AS avg_days,
                   ROUND(100.0 * AVG(CASE WHEN order_delivered_customer_date >
                    order_estimated_delivery_date THEN 1 ELSE 0 END)::numeric, 2)
                                                                       AS pct_late
            FROM orders
            WHERE order_status = 'delivered'
              AND order_delivered_customer_date IS NOT NULL
        """)).one()
        print(f"   Delivery: {delivery.delivered:,} orders, "
              f"avg {delivery.avg_days} days, {delivery.pct_late}% late")

        rfm_sample = conn.execute(text("""
            WITH per_person AS (
                SELECT c.customer_unique_id,
                       COUNT(DISTINCT o.order_id)               AS frequency,
                       SUM(p.payment_value)                     AS monetary
                FROM customers c
                JOIN orders o USING (customer_id)
                JOIN order_payments p USING (order_id)
                GROUP BY c.customer_unique_id
            )
            SELECT COUNT(*)                                                AS people,
                   ROUND(AVG(frequency)::numeric, 3)                       AS avg_freq,
                   ROUND(AVG(monetary)::numeric, 2)                        AS avg_monetary,
                   SUM(CASE WHEN frequency > 1 THEN 1 ELSE 0 END)          AS repeaters
            FROM per_person
        """)).one()
        print(f"   RFM sanity: {rfm_sample.people:,} people, "
              f"avg_freq={rfm_sample.avg_freq}, avg_monetary={rfm_sample.avg_monetary}, "
              f"repeaters={rfm_sample.repeaters:,}")

        # Read-only role can SELECT and has no data-modification privileges.
    print("\n[verify] read-only role:")
    with ro_engine.connect() as ro:
        n = ro.execute(text("SELECT COUNT(*) FROM orders")).scalar_one()
        print(f"   SELECT count(*) FROM orders as readonly = {n:,}  OK")

        write_privileges = ro.execute(text("""
            SELECT table_name,
                   has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'INSERT') AS can_insert,
                   has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'UPDATE') AS can_update,
                   has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'DELETE') AS can_delete,
                   has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'TRUNCATE') AS can_truncate
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)).all()
        violations = [row for row in write_privileges if any(row[1:])]
        if violations:
            sys.exit(f"[verify] SECURITY ISSUE: readonly write privileges detected: {violations}")
        print("   privilege metadata confirms INSERT/UPDATE/DELETE/TRUNCATE are denied")

    print("\n[verify] ALL CHECKS PASSED ✓")


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--bootstrap", action="store_true", help="Create/update dedicated roles and grants only.")
    modes.add_argument("--verify", action="store_true", help="Run read-only verification only.")
    modes.add_argument(
        "--tables",
        nargs="+",
        choices=LOAD_ORDER,
        help="Safely load only named empty tables; skips schema DDL and refuses to append.",
    )
    args = ap.parse_args()

    cfg = load_env(for_bootstrap=args.bootstrap)
    validate_connection_roles(cfg, require_admin=args.bootstrap)

    raw_dir = (ROOT / cfg["DATASET_RAW_DIR"]).resolve()
    if not raw_dir.is_dir():
        sys.exit(f"DATASET_RAW_DIR does not exist: {raw_dir}")

    if args.bootstrap:
        bootstrap(cfg)
        return

    app_engine = create_engine(cfg["DATABASE_URL"], future=True)
    ro_engine = create_engine(cfg["DATABASE_URL_READONLY"], future=True)

    if args.tables:
        targets = set(args.tables)
        validate_resume_state(app_engine, targets)
        frames = prepare_frames(raw_dir, targets)
        load_frames(app_engine, frames)
        verify_targets(app_engine, targets)
        return

    if not args.verify:
        apply_schema(app_engine)
        frames = prepare_frames(raw_dir)
        load_frames(app_engine, frames)

    verify(app_engine, ro_engine)


if __name__ == "__main__":
    main()
