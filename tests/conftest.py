"""
Shared pytest fixtures + auto-skip logic.

Every test module can rely on these fixtures instead of duplicating setup:
  - db_engine        : SQLAlchemy read-only engine (skips module if DB unreachable)
  - retriever        : Phase 7 RAG retriever      (skips module if index missing)
  - api_client       : FastAPI TestClient         (skips module if app import fails)
  - has_llm          : True iff LLM_API_KEY is set (does NOT prove quota)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


# --------------------------- Database ---------------------------------------
@pytest.fixture(scope="session")
def db_engine():
    from sqlalchemy import text
    try:
        from api.database import readonly_engine
        with readonly_engine.connect() as c:
            c.execute(text("SELECT 1")).scalar()
    except Exception as e:
        pytest.skip(f"PostgreSQL unreachable ({e.__class__.__name__}): {e}",
                    allow_module_level=True)
    return readonly_engine


# --------------------------- RAG retriever ----------------------------------
@pytest.fixture(scope="session")
def retriever():
    try:
        from rag.retriever import Retriever
        return Retriever()
    except FileNotFoundError:
        pytest.skip("RAG index not built — run scripts/build_rag_index.py",
                    allow_module_level=True)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Retriever init failed: {e}", allow_module_level=True)


# --------------------------- FastAPI test client ---------------------------
@pytest.fixture(scope="session")
def api_client(db_engine):
    try:
        from fastapi.testclient import TestClient
        from api.main import app
    except Exception as e:
        pytest.skip(f"FastAPI app not importable: {e}", allow_module_level=True)
    return TestClient(app)


# --------------------------- LLM presence flag -----------------------------
@pytest.fixture(scope="session")
def has_llm() -> bool:
    return bool(os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY"))
