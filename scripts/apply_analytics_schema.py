"""
Apply database/analytics_schema.sql to whatever database DATABASE_URL points at.
Used for cloud deployments where psql isn't installed locally.

Usage:
    DATABASE_URL="postgresql+psycopg://user:pw@host/db" \
        .venv/Scripts/python.exe scripts/apply_analytics_schema.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "database" / "analytics_schema.sql"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    # Render exposes URLs as postgresql:// — SQLAlchemy needs the driver.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = create_engine(url, future=True)
    sql_text = SQL_FILE.read_text(encoding="utf-8")

    # Execute the whole file as one script. Postgres allows multiple
    # CREATE OR REPLACE VIEW statements in a single execute() call via `exec_driver_sql`.
    with engine.begin() as conn:
        conn.exec_driver_sql(sql_text)
    print(f"Applied {SQL_FILE.name} to {engine.url.host}/{engine.url.database}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
