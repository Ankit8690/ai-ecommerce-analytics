"""Wraps scripts/test_rag_pipeline.py — every Case becomes a pytest test."""
from __future__ import annotations

import importlib
import pytest

_mod = importlib.import_module("scripts.test_rag_pipeline")
CASES = _mod.CASES


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_rag_case(case, retriever):
    ok, detail = case.fn()
    assert ok, detail
