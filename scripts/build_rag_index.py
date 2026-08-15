"""One-shot RAG index build. Run after any change to docs/*.md or DECISIONS.md."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag.ingest import build_index

if __name__ == "__main__":
    build_index(verbose=True)
