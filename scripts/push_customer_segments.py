"""
One-shot: copy analytics.customer_segments from LOCAL Postgres to REMOTE (Render).

Usage (Git Bash, from repo root):
    export REMOTE_DATABASE_URL="postgresql://ecommerce_app:pw@dpg-...oregon-postgres.render.com/ecommerce_ai_xxxx"
    .venv/Scripts/python.exe scripts/push_customer_segments.py

Reads from your local ecommerce_ai (via DATABASE_URL_READONLY in .env),
writes to REMOTE_DATABASE_URL. Idempotent — drops+recreates the remote table.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import pandas as pd
from sqlalchemy import create_engine, text


def _norm(url: str) -> str:
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def main() -> int:
    local_url = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
    remote_url = os.environ.get("REMOTE_DATABASE_URL")
    if not local_url:
        sys.exit("Set DATABASE_URL_READONLY (or DATABASE_URL) — your local DB.")
    if not remote_url:
        sys.exit("Set REMOTE_DATABASE_URL — Render External Database URL.")

    local_engine = create_engine(_norm(local_url))
    remote_engine = create_engine(_norm(remote_url))

    # 1. Pull from local
    print("[pull] reading analytics.customer_segments from local...", flush=True)
    df = pd.read_sql("SELECT * FROM analytics.customer_segments", local_engine)
    print(f"[pull] got {len(df):,} rows, columns: {list(df.columns)}")

    # 2. Push to remote — ensure schema exists, drop+recreate for idempotence.
    print("[push] writing to remote...", flush=True)
    with remote_engine.begin() as conn:
        conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS analytics")
        conn.exec_driver_sql("DROP TABLE IF EXISTS analytics.customer_segments")
    df.to_sql("customer_segments", remote_engine, schema="analytics",
              if_exists="fail", index=False, chunksize=5000, method="multi")

    # 3. Verify
    with remote_engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM analytics.customer_segments")).scalar()
    print(f"[verify] remote analytics.customer_segments now has {n:,} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
