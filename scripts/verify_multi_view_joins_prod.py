"""
Production-grade verification for multi-view join templates.

For every one of the 9 templates in ai/nl_interpreter.py:_JOIN_TEMPLATES,
this script independently derives the expected result from RAW public.* tables
using Pandas, then compares against what the template returns from Postgres.

Any mismatch is a real bug: either the template has wrong grain, wrong join
condition, wrong filter, or a row-multiplication trap.

Also covers:
  • HAVING filter — categories with <100 reviews must be excluded from
    the "avg_review_score by category" result.
  • JOIN vs LEFT JOIN — customers/orders without segments must not appear
    in "orders by segment", and the counts must add up correctly.
  • Row-multiplication trap — computing total GMV two independent ways
    and confirming they match exactly.
  • Grain check — average of averages vs true underlying average.

Run:
    .venv\\Scripts\\python.exe scripts/verify_multi_view_joins_prod.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import pandas as pd
from sqlalchemy import text

from api.database import readonly_engine
from ai.nl_interpreter import _JOIN_TEMPLATES


# ---------------------------------------------------------------------------
# Load raw source tables once — Pandas will do the reference computations.
# ---------------------------------------------------------------------------
def load_raw() -> dict:
    print("[setup] loading raw source tables from Postgres...")
    with readonly_engine.connect() as c:
        raw = {
            "customers":      pd.read_sql("SELECT customer_id, customer_unique_id, customer_state FROM public.customers", c),
            "orders":         pd.read_sql("SELECT order_id, customer_id, order_status, order_purchase_timestamp, order_delivered_customer_date FROM public.orders", c),
            "order_items":    pd.read_sql("SELECT order_id, product_id, price, freight_value FROM public.order_items", c),
            "order_reviews":  pd.read_sql("SELECT order_id, review_score FROM public.order_reviews", c),
            "products":       pd.read_sql("SELECT product_id, product_category_name FROM public.products", c),
            "segments":       pd.read_sql("SELECT customer_unique_id, segment_label FROM analytics.customer_segments", c),
        }
    for name, df in raw.items():
        print(f"  {name:16s} {len(df):>8,} rows")
    return raw


def run_template(metric: str, dim: str) -> pd.DataFrame:
    """Execute the template SQL and return results as a DataFrame."""
    sql = " ".join(_JOIN_TEMPLATES[(metric, dim)].split())
    with readonly_engine.connect() as c:
        return pd.read_sql(sql, c)


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------
def compare(name: str, expected: pd.Series, got: pd.Series, tol: float = 0.01) -> tuple[bool, str]:
    """
    Compare two Series indexed by the same grouping key. Returns (ok, detail).
    """
    # Reindex both to a common sorted key set
    all_keys = sorted(set(expected.index) | set(got.index))
    exp = expected.reindex(all_keys).astype(float).round(2)
    obs = got.reindex(all_keys).astype(float).round(2)
    diff = (exp - obs).abs().fillna(999999)
    fail_keys = diff[diff > tol].index.tolist()
    if fail_keys:
        details = []
        for k in fail_keys[:5]:
            details.append(f"    {k}: expected={exp.get(k)!r} got={obs.get(k)!r} diff={diff[k]}")
        return False, f"{len(fail_keys)} key(s) mismatch:\n" + "\n".join(details)
    return True, f"all {len(all_keys)} keys match within tolerance {tol}"


# ---------------------------------------------------------------------------
# Test cases — one per template + edge cases
# ---------------------------------------------------------------------------
def test_order_count_by_segment(raw):
    """Independent derivation of orders per segment via Pandas merge."""
    exp = (raw["orders"]
           .merge(raw["customers"], on="customer_id")
           .merge(raw["segments"], on="customer_unique_id")
           .groupby("segment_label")["order_id"]
           .nunique())
    got = run_template("order_count", "segment").set_index("segment")["order_count"]
    return compare("order_count_by_segment", exp, got)


def test_avg_delivery_days_by_segment(raw):
    delivered = raw["orders"][
        (raw["orders"]["order_status"] == "delivered")
        & (raw["orders"]["order_delivered_customer_date"].notna())
    ].copy()
    delivered["delivery_days"] = (
        pd.to_datetime(delivered["order_delivered_customer_date"])
        - pd.to_datetime(delivered["order_purchase_timestamp"])
    ).dt.total_seconds() / 86400.0
    merged = (delivered
              .merge(raw["customers"], on="customer_id")
              .merge(raw["segments"], on="customer_unique_id"))
    exp = merged.groupby("segment_label")["delivery_days"].mean().round(2)
    got = run_template("avg_delivery_days", "segment").set_index("segment")["avg_delivery_days"]
    return compare("avg_delivery_days_by_segment", exp, got, tol=0.05)


def test_avg_review_score_by_segment(raw):
    merged = (raw["order_reviews"]
              .merge(raw["orders"][["order_id", "customer_id"]], on="order_id")
              .merge(raw["customers"], on="customer_id")
              .merge(raw["segments"], on="customer_unique_id"))
    exp = merged.groupby("segment_label")["review_score"].mean().round(2)
    got = run_template("avg_review_score", "segment").set_index("segment")["avg_review_score"]
    return compare("avg_review_score_by_segment", exp, got, tol=0.05)


def test_total_gmv_by_segment(raw):
    items = raw["order_items"].copy()
    items["gmv"] = items["price"].astype(float) + items["freight_value"].astype(float)
    merged = (items
              .merge(raw["orders"][["order_id", "customer_id"]], on="order_id")
              .merge(raw["customers"], on="customer_id")
              .merge(raw["segments"], on="customer_unique_id"))
    exp = merged.groupby("segment_label")["gmv"].sum().round(2)
    got = run_template("total_gmv", "segment").set_index("segment")["total_gmv"]
    return compare("total_gmv_by_segment", exp, got, tol=1.0)


def test_order_count_by_state(raw):
    merged = raw["orders"].merge(raw["customers"], on="customer_id")
    exp = merged.groupby("customer_state")["order_id"].nunique()
    got = run_template("order_count", "state").set_index("state")["order_count"]
    return compare("order_count_by_state", exp, got)


def test_avg_delivery_days_by_state(raw):
    delivered = raw["orders"][
        (raw["orders"]["order_status"] == "delivered")
        & (raw["orders"]["order_delivered_customer_date"].notna())
    ].copy()
    delivered["delivery_days"] = (
        pd.to_datetime(delivered["order_delivered_customer_date"])
        - pd.to_datetime(delivered["order_purchase_timestamp"])
    ).dt.total_seconds() / 86400.0
    merged = delivered.merge(raw["customers"], on="customer_id")
    exp = merged.groupby("customer_state")["delivery_days"].mean().round(2)
    got = run_template("avg_delivery_days", "state").set_index("state")["avg_delivery_days"]
    return compare("avg_delivery_days_by_state", exp, got, tol=0.05)


def test_total_gmv_by_state(raw):
    items = raw["order_items"].copy()
    items["gmv"] = items["price"].astype(float) + items["freight_value"].astype(float)
    merged = (items
              .merge(raw["orders"][["order_id", "customer_id"]], on="order_id")
              .merge(raw["customers"], on="customer_id"))
    exp = merged.groupby("customer_state")["gmv"].sum().round(2)
    got = run_template("total_gmv", "state").set_index("state")["total_gmv"]
    return compare("total_gmv_by_state", exp, got, tol=1.0)


def test_total_gmv_by_category(raw):
    items = raw["order_items"].copy()
    items["gmv"] = items["price"].astype(float) + items["freight_value"].astype(float)
    merged = items.merge(raw["products"], on="product_id")
    merged = merged[merged["product_category_name"].notna()]
    exp = merged.groupby("product_category_name")["gmv"].sum().round(2)
    got_df = run_template("total_gmv", "category")
    got = got_df.set_index("category")["total_gmv"]
    # Template limits to LIMIT 25 — only compare top 25 in expected
    exp_top25 = exp.sort_values(ascending=False).head(25)
    return compare("total_gmv_by_category (top 25)", exp_top25, got, tol=1.0)


def test_avg_review_score_by_category(raw):
    merged = (raw["order_items"]
              .merge(raw["products"], on="product_id")
              .merge(raw["order_reviews"], on="order_id"))
    merged = merged[merged["product_category_name"].notna()]
    grp = merged.groupby("product_category_name")["review_score"]
    counts = grp.count()
    # Template has HAVING >= 100 — expected must apply the same filter
    valid_cats = counts[counts >= 100].index
    exp = grp.mean().loc[valid_cats].round(2)
    got_df = run_template("avg_review_score", "category")
    got = got_df.set_index("category")["avg_review_score"]
    # Template also LIMITs to 25 — compare within intersection
    common = sorted(set(exp.index) & set(got.index))
    return compare("avg_review_score_by_category (HAVING>=100 & LIMIT 25)",
                   exp.reindex(common), got.reindex(common), tol=0.05)


# ---------------------------------------------------------------------------
# Edge-case tests
# ---------------------------------------------------------------------------
def test_no_row_multiplication_on_gmv(raw):
    """
    Compute total GMV two ways:
      (A) via the by-state template summing to a scalar
      (B) directly from public.order_items (independent, no joins)
    If joins introduce row multiplication these will differ.
    """
    items = raw["order_items"].copy()
    items["gmv"] = items["price"].astype(float) + items["freight_value"].astype(float)
    raw_total = items["gmv"].sum()

    template_result = run_template("total_gmv", "state")
    template_total = float(template_result["total_gmv"].sum())

    diff = abs(raw_total - template_total)
    ok = diff < 1.0
    detail = f"raw={raw_total:,.2f}  template_sum={template_total:,.2f}  diff={diff:.2f}"
    return ok, detail


def test_having_filter_actually_filters(raw):
    """The 'avg_review_score by category' template has HAVING >= 100.
    Confirm no category with <100 reviews appears in the output."""
    got = run_template("avg_review_score", "category")
    categories_returned = set(got["category"].tolist())

    # Count reviews per category from raw
    merged = raw["order_items"].merge(raw["products"], on="product_id").merge(raw["order_reviews"], on="order_id")
    review_counts = merged.groupby("product_category_name")["review_score"].count()
    small_cats = set(review_counts[review_counts < 100].index)

    leaked = categories_returned & small_cats
    ok = len(leaked) == 0
    detail = f"template returned {len(categories_returned)} categories; "
    detail += f"{len(small_cats)} have <100 reviews; leaked={sorted(leaked) or 'none'}"
    return ok, detail


def test_segment_inner_join_semantics(raw):
    """
    'orders by segment' uses INNER JOIN. Any customer without a segment row
    is excluded. Verify the returned total equals the count of orders that
    can actually be matched to a segment.
    """
    matched = (raw["orders"]
               .merge(raw["customers"], on="customer_id")
               .merge(raw["segments"], on="customer_unique_id"))
    expected_total_orders = matched["order_id"].nunique()

    got = run_template("order_count", "segment")
    template_total = int(got["order_count"].sum())

    ok = expected_total_orders == template_total
    detail = (f"orders reachable via segment JOIN chain = {expected_total_orders:,}; "
              f"template sum = {template_total:,}; "
              f"total orders in DB = {raw['orders']['order_id'].nunique():,}")
    return ok, detail


def test_all_segments_present(raw):
    """No segment should be silently dropped from any template that groups
    by segment. The customer_segments table has 4 labels — every template
    must return all 4."""
    expected_segments = set(raw["segments"]["segment_label"].unique())
    fails = []
    for metric in ("order_count", "avg_delivery_days", "avg_review_score", "total_gmv"):
        got = run_template(metric, "segment")
        got_segs = set(got["segment"].tolist())
        missing = expected_segments - got_segs
        if missing:
            fails.append(f"{metric}: missing {sorted(missing)}")
    ok = len(fails) == 0
    detail = "; ".join(fails) if fails else f"all {len(expected_segments)} segments present in all 4 templates"
    return ok, detail


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main() -> int:
    raw = load_raw()

    cases = [
        ("template  order_count by segment",        lambda: test_order_count_by_segment(raw)),
        ("template  avg_delivery_days by segment",  lambda: test_avg_delivery_days_by_segment(raw)),
        ("template  avg_review_score by segment",   lambda: test_avg_review_score_by_segment(raw)),
        ("template  total_gmv by segment",          lambda: test_total_gmv_by_segment(raw)),
        ("template  order_count by state",          lambda: test_order_count_by_state(raw)),
        ("template  avg_delivery_days by state",    lambda: test_avg_delivery_days_by_state(raw)),
        ("template  total_gmv by state",            lambda: test_total_gmv_by_state(raw)),
        ("template  total_gmv by category",         lambda: test_total_gmv_by_category(raw)),
        ("template  avg_review_score by category",  lambda: test_avg_review_score_by_category(raw)),
        ("edge      no row-multiplication on GMV",  lambda: test_no_row_multiplication_on_gmv(raw)),
        ("edge      HAVING filter actually filters", lambda: test_having_filter_actually_filters(raw)),
        ("edge      segment INNER JOIN semantics",  lambda: test_segment_inner_join_semantics(raw)),
        ("edge      all segments present",          lambda: test_all_segments_present(raw)),
    ]

    print(f"\nRunning {len(cases)} production-grade verifications...\n")
    passed = 0
    failed = []
    for name, fn in cases:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"exception: {type(e).__name__}: {e}"
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {name}")
        print(f"         {detail}")
        if ok:
            passed += 1
        else:
            failed.append(name)

    print(f"\n{'='*70}")
    print(f"RESULT: {passed}/{len(cases)} passed")
    print(f"{'='*70}")
    if failed:
        print(f"\nFAILED: {failed}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
