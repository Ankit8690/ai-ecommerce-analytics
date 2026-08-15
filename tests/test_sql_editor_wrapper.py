"""Wraps scripts/test_sql_editor_100.py — every Case becomes a pytest test."""
from __future__ import annotations

import importlib
import pytest

_mod = importlib.import_module("scripts.test_sql_editor_100")
CASES = _mod.CASES
run_pipeline = _mod.run_pipeline


@pytest.mark.db
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_sql_editor_case(case, db_engine):
    status, rows, err = run_pipeline(case.sql, row_cap=case.row_cap)
    # Status match
    assert case.expected_status == status, \
        f"expected status={case.expected_status} got={status} err={err!r}"
    if status != "ok":
        if case.err_contains:
            assert case.err_contains.lower() in (err or "").lower(), \
                f"err missing '{case.err_contains}': {err!r}"
        return
    # OK path
    if case.expected_rows is not None:
        assert len(rows) == case.expected_rows, \
            f"expected {case.expected_rows} rows got {len(rows)}"
    if case.min_rows is not None:
        assert len(rows) >= case.min_rows, \
            f"expected >= {case.min_rows} rows got {len(rows)}"
    if case.checker is not None:
        ok, detail = case.checker(rows)
        assert ok, f"checker failed: {detail}"
