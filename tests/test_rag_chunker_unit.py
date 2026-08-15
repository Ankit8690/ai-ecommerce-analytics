"""RAG chunker unit tests — no index required, no DB, no LLM."""
from __future__ import annotations

import pytest

from rag.ingest import chunk_markdown, Chunk


def test_heading_stack_produces_dotted_path():
    md = ("# Top\n## Sub\n### Leaf\n"
          "A body paragraph long enough to survive the minimum-word filter with room to spare "
          "so this ends up as a single retrievable chunk under Top > Sub > Leaf.")
    chunks = chunk_markdown(md, "src.md")
    assert len(chunks) == 1
    assert chunks[0].section == "Top > Sub > Leaf"
    assert chunks[0].source == "src.md"


def test_short_body_dropped():
    assert chunk_markdown("# H\nToo short.", "s.md") == []


def test_no_headings_returns_top_chunk_when_long_enough():
    body = " ".join(["word"] * 40)
    chunks = chunk_markdown(body, "s.md")
    assert len(chunks) == 1
    assert chunks[0].section in ("(top)", "(preamble)")


def test_preamble_before_first_heading_captured():
    preamble = " ".join(["preamble"] * 30)
    body = " ".join(["body"] * 30)
    md = f"{preamble}\n\n# H\n{body}"
    chunks = chunk_markdown(md, "s.md")
    sections = [c.section for c in chunks]
    assert "(preamble)" in sections and "H" in sections


def test_long_section_is_split():
    long_body = " ".join(["word"] * 900)
    md = f"# H\n{long_body}"
    chunks = chunk_markdown(md, "s.md")
    assert len(chunks) >= 2, f"expected split, got {len(chunks)}"
    for c in chunks:
        assert c.word_count <= 400


def test_fingerprint_dedup_within_document():
    body = " ".join(["identical"] * 30)
    md = f"# A\n{body}\n\n# B\n{body}"
    chunks = chunk_markdown(md, "s.md")
    # Both sections have identical body → same fingerprint → dedup keeps one
    fps = [c.fingerprint for c in chunks]
    assert len(fps) == len(set(fps))


def test_chunk_make_populates_ids_and_word_count():
    c = Chunk.make("s.md", "H", "one two three four five six seven eight nine ten")
    assert c.word_count == 10
    assert len(c.id) == 12 and len(c.fingerprint) == 12
    assert c.source == "s.md" and c.section == "H"
