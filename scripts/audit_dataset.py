"""
Phase 1 dataset audit.

Reads the raw CSVs (never modifies them) and prints a full audit:
row counts, dtypes, nulls, duplicates, PK/FK integrity, categorical
distributions, numerical percentiles, date ranges, and known anomalies.

Usage:  python scripts/audit_dataset.py
Output: prints to stdout; redirect to a file if needed.
"""
from __future__ import annotations
import glob, os, sys
import pandas as pd

RAW = os.path.join(os.path.dirname(__file__), "..", "Dataset", "raw")
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)


def hdr(title: str) -> None:
    print("\n" + "=" * 78 + f"\n {title}\n" + "=" * 78)


def load() -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}
    for path in sorted(glob.glob(os.path.join(RAW, "*.csv"))):
        name = os.path.splitext(os.path.basename(path))[0]
        dfs[name] = pd.read_csv(path, low_memory=False)
    return dfs


def parse_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="%m/%d/%Y %H:%M", errors="coerce")


def profile(name: str, df: pd.DataFrame) -> None:
    hdr(f"{name}  rows={len(df):,}  cols={len(df.columns)}  exact_dup_rows={df.duplicated().sum():,}")
    info = pd.DataFrame({
        "dtype": df.dtypes.astype(str),
        "nulls": df.isna().sum(),
        "null_%": (df.isna().mean() * 100).round(2),
        "nunique": df.nunique(),
        "sample": [df[c].dropna().iloc[0] if df[c].notna().any() else None for c in df.columns],
    })
    print(info.to_string())


def numeric_stats(name: str, df: pd.DataFrame, cols: list[str]) -> None:
    hdr(f"{name} — numeric percentiles")
    print(df[cols].describe(percentiles=[.01, .05, .25, .5, .75, .95, .99]).round(2).to_string())


def top_values(name: str, s: pd.Series, k: int = 10) -> None:
    hdr(f"{name} — top {k} of {s.nunique()} values")
    print(s.value_counts(dropna=False).head(k).to_string())


def fk(label: str, child: pd.DataFrame, ccol: str, parent: pd.DataFrame, pcol: str) -> None:
    missing = ~child[ccol].isin(set(parent[pcol]))
    print(f"  {label:<52} orphans={missing.sum():>6,}  ({missing.mean()*100:5.2f}%)")


def main() -> None:
    dfs = load()
    for n, df in dfs.items():
        profile(n, df)

    C, G, O, OI, OP, RV, P, S = (
        dfs["CUSTOMERS"], dfs["GEO_LOCATION"], dfs["ORDERS"], dfs["ORDER_ITEMS"],
        dfs["ORDER_PAYMENTS"], dfs["ORDER_REVIEW_RATINGS"], dfs["PRODUCTS"], dfs["SELLERS"],
    )

    hdr("FOREIGN KEY INTEGRITY")
    fk("ORDERS.customer_id -> CUSTOMERS.customer_id", O, "customer_id", C, "customer_id")
    fk("ORDER_ITEMS.order_id -> ORDERS.order_id", OI, "order_id", O, "order_id")
    fk("ORDER_ITEMS.product_id -> PRODUCTS.product_id", OI, "product_id", P, "product_id")
    fk("ORDER_ITEMS.seller_id -> SELLERS.seller_id", OI, "seller_id", S, "seller_id")
    fk("ORDER_PAYMENTS.order_id -> ORDERS.order_id", OP, "order_id", O, "order_id")
    fk("REVIEWS.order_id -> ORDERS.order_id", RV, "order_id", O, "order_id")
    fk("CUSTOMERS.zip -> GEO.zip", C, "customer_zip_code_prefix", G, "geolocation_zip_code_prefix")
    fk("SELLERS.zip -> GEO.zip", S, "seller_zip_code_prefix", G, "geolocation_zip_code_prefix")

    hdr("CANDIDATE PRIMARY KEYS")
    candidates = [
        ("CUSTOMERS", C, ["customer_id"]),
        ("GEO_LOCATION", G, ["geolocation_zip_code_prefix"]),
        ("ORDERS", O, ["order_id"]),
        ("ORDER_ITEMS", OI, ["order_id", "order_item_id"]),
        ("ORDER_PAYMENTS", OP, ["order_id", "payment_sequential"]),
        ("REVIEWS (review_id)", RV, ["review_id"]),
        ("REVIEWS (order_id)", RV, ["order_id"]),
        ("REVIEWS (pair)", RV, ["review_id", "order_id"]),
        ("PRODUCTS", P, ["product_id"]),
        ("SELLERS", S, ["seller_id"]),
    ]
    for nm, df, cols in candidates:
        d = df.duplicated(subset=cols).sum()
        print(f"  {nm:<22} {str(cols):<38} duplicates={d:>6,}  {'UNIQUE' if d == 0 else 'NOT UNIQUE'}")

    hdr("ORDER COVERAGE")
    print(f"  orders with >= 1 item     : {O.order_id.isin(set(OI.order_id)).sum():>6,} / {len(O):,}")
    print(f"  orders with >= 1 payment  : {O.order_id.isin(set(OP.order_id)).sum():>6,} / {len(O):,}")
    print(f"  orders with >= 1 review   : {O.order_id.isin(set(RV.order_id)).sum():>6,} / {len(O):,}")
    print(f"  products ever sold        : {P.product_id.isin(set(OI.product_id)).sum():>6,} / {len(P):,}")
    print(f"  sellers ever sold         : {S.seller_id.isin(set(OI.seller_id)).sum():>6,} / {len(S):,}")

    hdr("DATE RANGES (M/D/YYYY H:MM)")
    for df_, cols_, nm in [
        (O, ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
             "order_delivered_customer_date", "order_estimated_delivery_date"], "ORDERS"),
        (OI, ["shipping_limit_date"], "ORDER_ITEMS"),
        (RV, ["review_creation_date", "review_answer_timestamp"], "REVIEWS"),
    ]:
        for c in cols_:
            s = parse_dt(df_[c])
            unparsed = df_[c].notna() & s.isna()
            print(f"  {nm}.{c:<32} {str(s.min())[:16]} -> {str(s.max())[:16]}  unparsed={unparsed.sum()}")

    hdr("ORDER STATUS")
    print(O.order_status.value_counts().to_string())

    hdr("PAYMENT TYPES + INSTALLMENTS")
    print(OP.payment_type.value_counts().to_string())
    print("\ninstallments distribution:")
    print(OP.payment_installments.value_counts().sort_index().to_string())

    numeric_stats("ORDER_ITEMS", OI, ["price", "freight_value", "order_item_id"])
    numeric_stats("ORDER_PAYMENTS", OP, ["payment_value", "payment_installments"])
    numeric_stats(
        "PRODUCTS",
        P,
        ["product_name_lenght", "product_description_lenght", "product_photos_qty",
         "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"],
    )

    hdr("REVIEW SCORE DISTRIBUTION")
    vc = RV.review_score.value_counts().sort_index()
    print(vc.to_string())
    print(f"  negative (<=2): {vc.loc[[1,2]].sum():,} ({vc.loc[[1,2]].sum()/vc.sum()*100:.2f}%)")
    print(f"  positive (>=4): {vc.loc[[4,5]].sum():,} ({vc.loc[[4,5]].sum()/vc.sum()*100:.2f}%)")

    hdr("REVIEW TABLE ANOMALY")
    print(f"  rows={len(RV):,} unique review_id={RV.review_id.nunique():,} "
          f"unique order_id={RV.order_id.nunique():,} orders={len(O):,}")
    print(f"  duplicate order_id rows: {RV.duplicated('order_id', keep=False).sum():,}")
    print(f"  duplicate review_id rows: {RV.duplicated('review_id', keep=False).sum():,}")

    hdr("REPEAT PURCHASE (customer_unique_id)")
    oc = O.merge(C[["customer_id", "customer_unique_id"]], on="customer_id")
    cnt = oc.groupby("customer_unique_id").order_id.nunique()
    print(cnt.value_counts().sort_index().head(10).to_string())
    print(f"  unique customers = {len(cnt):,}")
    print(f"  repeat (>1 order) = {(cnt > 1).sum():,} ({(cnt > 1).mean() * 100:.2f}%)")
    print(f"  max orders by one customer = {cnt.max()}")

    hdr("DELIVERY PERFORMANCE (delivered orders)")
    d = O[O.order_status == "delivered"].copy()
    for c in ["order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date"]:
        d[c] = parse_dt(d[c])
    d["days_to_deliver"] = (d.order_delivered_customer_date - d.order_purchase_timestamp).dt.total_seconds() / 86400
    d["delay_vs_est"] = (d.order_delivered_customer_date - d.order_estimated_delivery_date).dt.total_seconds() / 86400
    print(d[["days_to_deliver", "delay_vs_est"]].describe(percentiles=[.5, .9, .95, .99]).round(2).to_string())
    print(f"  late deliveries: {(d.delay_vs_est > 0).mean() * 100:.2f}%")
    print(f"  delivered but null delivered_customer_date: {d.order_delivered_customer_date.isna().sum()}")

    hdr("PAYMENT vs ITEM TOTAL RECONCILIATION")
    it = OI.groupby("order_id").apply(lambda g: (g.price + g.freight_value).sum(), include_groups=False).rename("item_total")
    pv = OP.groupby("order_id").payment_value.sum().rename("paid")
    m = pd.concat([it, pv], axis=1).dropna()
    m["diff"] = (m.paid - m.item_total).round(2)
    print(f"  orders compared: {len(m):,}")
    print(f"  exact match (|diff|<0.01): {(m['diff'].abs() < 0.01).sum():,}  ({(m['diff'].abs() < 0.01).mean() * 100:.2f}%)")
    print(f"  |diff| > 1: {(m['diff'].abs() > 1).sum():,}")
    print(m['diff'].describe().round(2).to_string())

    hdr("GEOGRAPHY SANITY")
    print(f"  lat range {G.geolocation_lat.min():.4f} -> {G.geolocation_lat.max():.4f}")
    print(f"  lng range {G.geolocation_lng.min():.4f} -> {G.geolocation_lng.max():.4f}")
    print(f"  customer states ({C.customer_state.nunique()}): {sorted(C.customer_state.unique())}")
    j = C.merge(G, left_on="customer_zip_code_prefix", right_on="geolocation_zip_code_prefix", how="inner")
    print(f"  zip matched rows: {len(j):,}  city agreement: {(j.customer_city == j.geolocation_city).mean() * 100:.2f}%"
          f"  state agreement: {(j.customer_state == j.geolocation_state).mean() * 100:.2f}%")

    top_values("PRODUCT CATEGORIES", P.product_category_name, k=15)
    top_values("CUSTOMER STATES (by count)", C.customer_state, k=10)
    top_values("SELLER STATES (by count)", S.seller_state, k=10)

    hdr("ORDERS PER MONTH (purchase)")
    pm = parse_dt(O.order_purchase_timestamp).dt.to_period("M").value_counts().sort_index()
    print(pm.to_string())

    hdr("FREIGHT-TO-PRICE RATIO")
    r = (OI.freight_value / OI.price.replace(0, pd.NA)).dropna()
    print(r.describe(percentiles=[.5, .9, .95, .99]).round(3).to_string())

    hdr("PRODUCTS NEVER SOLD / SELLERS INACTIVE / ORDERS WITHOUT ITEMS")
    print(f"  products never appearing in ORDER_ITEMS: {(~P.product_id.isin(set(OI.product_id))).sum():,}")
    print(f"  sellers never appearing in ORDER_ITEMS : {(~S.seller_id.isin(set(OI.seller_id))).sum():,}")
    print(f"  orders never appearing in ORDER_ITEMS  : {(~O.order_id.isin(set(OI.order_id))).sum():,}")
    print(f"  orders never appearing in ORDER_PAYMENTS: {(~O.order_id.isin(set(OP.order_id))).sum():,}")

    hdr("DONE")


if __name__ == "__main__":
    main()
