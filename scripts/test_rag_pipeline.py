"""
Phase 7 RAG pipeline tests.
Covers: ingestion, chunking, dedup, index roundtrip, retrieval relevance,
empty/off-topic queries, source citation, deterministic (no-LLM) grounding.

Run: .venv\\Scripts\\python.exe scripts/test_rag_pipeline.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag.ingest import (build_index, chunk_markdown, load_index,
                        DEFAULT_SOURCES, INDEX_DIR, Chunk)
from rag.retriever import Retriever
from rag.synthesizer import answer_from_context


@dataclass
class Case:
    name: str
    fn: Callable[[], tuple[bool, str]]


CASES: list[Case] = []


def case(fn):
    CASES.append(Case(name=fn.__name__, fn=fn))
    return fn


# --- Ingestion / chunking -----------------------------------------------------
@case
def test_default_sources_exist():
    missing = [str(p) for p in DEFAULT_SOURCES if not p.exists()]
    return (not missing, f"missing sources: {missing}" if missing else "")


@case
def test_index_roundtrip():
    chunks, vec, mat = load_index()
    return (len(chunks) > 20 and mat.shape[0] == len(chunks),
            f"chunks={len(chunks)} shape={mat.shape}")


@case
def test_chunk_word_counts_bounded():
    chunks, _, _ = load_index()
    bad = [c for c in chunks if c.word_count < 5 or c.word_count > 500]
    return (len(bad) == 0, f"{len(bad)} chunks out of range")


@case
def test_chunks_carry_source_and_section():
    chunks, _, _ = load_index()
    bad = [c for c in chunks if not c.source or not c.section]
    return (len(bad) == 0, f"{len(bad)} chunks missing source/section")


@case
def test_chunk_dedup_by_fingerprint():
    chunks, _, _ = load_index()
    fps = [c.fingerprint for c in chunks]
    return (len(fps) == len(set(fps)),
            f"{len(fps) - len(set(fps))} duplicate fingerprints")


@case
def test_manual_chunk_short_body():
    md = "# Title\n\nOnly two words."
    out = chunk_markdown(md, "test.md")
    return (len(out) == 0, f"expected 0 short chunks, got {len(out)}")


@case
def test_manual_chunk_produces_section_path():
    md = "# H1\n## H2\nSome descriptive body text with enough words to exceed the minimum threshold for a chunk to be retained by the ingest pipeline safely."
    out = chunk_markdown(md, "test.md")
    return (len(out) == 1 and "H1 > H2" in out[0].section,
            f"got {[c.section for c in out]}")


# --- Retrieval relevance ------------------------------------------------------
@case
def test_retrieval_gmv_definition():
    r = Retriever()
    res = r.retrieve("What is Product GMV?", k=3)
    ok = any("gmv" in x.chunk.text.lower() or "gross merchandise" in x.chunk.text.lower()
             for x in res)
    return (ok and len(res) > 0,
            f"top hits: {[x.citation() for x in res[:3]]}")


@case
def test_retrieval_negative_review_rule():
    r = Retriever()
    res = r.retrieve("How is a negative review defined?", k=3)
    ok = any(("review_score" in x.chunk.text and "2" in x.chunk.text)
             or "negative" in x.chunk.text.lower()
             for x in res)
    return (ok, f"top hits: {[x.citation() for x in res[:3]]}")


@case
def test_retrieval_churn_decision():
    r = Retriever()
    res = r.retrieve("Why is churn not modelled in this project?", k=3)
    txt = " ".join(x.chunk.text.lower() for x in res)
    ok = "churn" in txt or "3.12" in txt or "d-007" in txt
    return (ok, f"top hits: {[x.citation() for x in res[:3]]}")


@case
def test_retrieval_dataset_provenance():
    r = Retriever()
    res = r.retrieve("Where does the dataset come from?", k=3)
    txt = " ".join(x.chunk.text.lower() for x in res)
    ok = "olist" in txt or "brazil" in txt or "relabel" in txt or "public" in txt
    return (ok, f"top hits: {[x.citation() for x in res[:3]]}")


@case
def test_retrieval_read_only_user():
    r = Retriever()
    res = r.retrieve("What safety controls exist around the SQL validator?", k=3)
    txt = " ".join(x.chunk.text.lower() for x in res)
    ok = "read-only" in txt or "readonly" in txt or "select" in txt
    return (ok, f"top hits: {[x.citation() for x in res[:3]]}")


@case
def test_retrieval_returns_scores_desc():
    r = Retriever()
    res = r.retrieve("data dictionary orders", k=5)
    if len(res) < 2:
        return True, "fewer than 2 results"
    ok = all(res[i].score >= res[i + 1].score for i in range(len(res) - 1))
    return (ok, "scores not monotonically descending")


# --- Negative / edge cases ----------------------------------------------------
@case
def test_empty_query_returns_empty():
    r = Retriever()
    return (r.retrieve("") == [] and r.retrieve("   ") == [],
            "empty query returned results")


@case
def test_offtopic_query_low_relevance():
    r = Retriever()
    res = r.retrieve("weather forecast in Tokyo tomorrow", k=3, min_score=0.05)
    # Either empty or top score much lower than a topical query's top score
    topical = r.retrieve("GMV definition", k=1, min_score=0.0)
    off_top = res[0].score if res else 0.0
    top_top = topical[0].score if topical else 0.0
    return (off_top < top_top,
            f"off-topic top={off_top:.3f} vs topical top={top_top:.3f}")


@case
def test_min_score_filter_applied():
    r = Retriever()
    high = r.retrieve("orders", k=10, min_score=0.99)
    return (len(high) == 0, f"expected 0 hits at 0.99 threshold, got {len(high)}")


# --- Synthesis / grounding ----------------------------------------------------
@case
def test_synthesis_empty_results_returns_no_results_message():
    ans, mode = answer_from_context("random unrelated query", [])
    return (mode == "no_results" and "No knowledge-base results" in ans,
            f"mode={mode}")


@case
def test_synthesis_fallback_cites_sources():
    r = Retriever()
    res = r.retrieve("Product GMV definition", k=3)
    # Force the deterministic path by importing directly
    from rag.synthesizer import _deterministic_answer
    ans = _deterministic_answer("What is GMV?", res)
    ok = all(rr.citation().split(" §")[0] in ans for rr in res)
    return (ok, "citations missing from fallback")


@case
def test_synthesis_never_leaks_prompt():
    """The fallback answer must not include the internal system prompt text."""
    r = Retriever()
    res = r.retrieve("orders", k=2)
    ans, _ = answer_from_context("orders", res)
    return ("You are the knowledge assistant" not in ans,
            "system prompt leaked into answer")


@case
def test_stats_contains_expected_sources():
    r = Retriever()
    stats = r.stats()
    expected = {"docs/data_dictionary.md", "docs/data_quality_report.md",
                "docs/database_relationships.md", "DECISIONS.md"}
    got = set(stats["sources"])
    return (expected.issubset(got),
            f"missing: {expected - got}")


@case
def test_ingest_produces_min_chunks():
    chunks, _, _ = build_index(verbose=False)
    return (len(chunks) >= 40, f"only {len(chunks)} chunks produced")


# --- Runner -------------------------------------------------------------------
def main() -> int:
    print(f"Running {len(CASES)} RAG pipeline tests...\n")
    passed = 0
    failed: list[tuple[str, str]] = []
    for i, c in enumerate(CASES, 1):
        try:
            ok, detail = c.fn()
        except Exception as e:
            ok, detail = False, f"exception: {type(e).__name__}: {e}"
        tag = "PASS" if ok else "FAIL"
        print(f"[{i:02d}] {tag}  {c.name}")
        if not ok:
            print(f"       -> {detail}")
            failed.append((c.name, detail))
        else:
            passed += 1
    pct = 100.0 * passed / len(CASES)
    print(f"\n{'='*60}\nRESULT: {passed}/{len(CASES)} passed ({pct:.1f}%)\n{'='*60}")
    if failed:
        for n, d in failed:
            print(f"  - {n}: {d}")
    return 0 if pct >= 95.0 else 1


if __name__ == "__main__":
    sys.exit(main())
