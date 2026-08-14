"""
Database connectivity module using dedicated read-only role (ecommerce_readonly).
"""
from __future__ import annotations

import os
from typing import Generator
from dotenv import load_dotenv
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Connection

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Default to read-only database URL for analytics security
READONLY_URL = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
if not READONLY_URL:
    raise RuntimeError("DATABASE_URL_READONLY or DATABASE_URL must be defined in .env")

# Engine using read-only credentials
readonly_engine: Engine = create_engine(READONLY_URL, future=True, pool_pre_ping=True)


def get_db() -> Generator[Connection, None, None]:
    """FastAPI dependency yielding a read-only database connection."""
    with readonly_engine.connect() as conn:
        yield conn


def check_db_health() -> bool:
    """Check read-only database connectivity."""
    try:
        with readonly_engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar_one()
        return True
    except Exception:
        return False
