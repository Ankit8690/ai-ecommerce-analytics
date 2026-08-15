"""Wraps scripts/test_decision_support.py — every Case becomes a pytest test."""
from __future__ import annotations

import importlib
import pytest

_mod = importlib.import_module("scripts.test_decision_support")
CASES = _mod.CASES


@pytest.mark.db
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_decision_support_case(case, db_engine):
    ok, detail = case.fn()
    assert ok, detail
