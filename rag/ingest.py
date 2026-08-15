"""
RAG ingestion: read markdown knowledge sources, split into heading-aware chunks,
fit a TF-IDF vectorizer, persist the index to disk.

Run once (or after any docs change):
    .venv\\Scripts\\python.exe scripts/build_rag_index.py
"""
from __future__ import annotations

import hashlib
import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from sklearn.feature_extraction.text import TfidfVectorizer

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "rag" / "index"
INDEX_DIR.mkdir(parents=True, exist_ok=True)

CHUNKS_PATH = INDEX_DIR / "chunks.pkl"
VECTORIZER_PATH = INDEX_DIR / "vectorizer.pkl"
MATRIX_PATH = INDEX_DIR / "matrix.pkl"

# Knowledge sources — docs describing business rules / definitions / decisions.
# Data-quality report, data dictionary, ERD/joins, and architecture decisions.
DEFAULT_SOURCES: list[Path] = [
    ROOT / "docs" / "data_dictionary.md",
    ROOT / "docs" / "data_quality_report.md",
    ROOT / "docs" / "database_relationships.md",
    ROOT / "DECISIONS.md",
]

# Chunk sizing
_TARGET_WORDS = 220     # aim per chunk
_MIN_WORDS = 20         # discard trivially small chunks
_MAX_WORDS = 400        # split overly long sections


@dataclass
class Chunk:
    """One retrievable knowledge unit."""
    id: str
    source: str          # e.g. "docs/data_dictionary.md"
    section: str         # heading path, e.g. "## public.order_reviews > Columns"
    text: str
    word_count: int
    fingerprint: str = field(default="")  # md5 of text for dedup

    @staticmethod
    def make(source: str, section: str, text: str) -> "Chunk":
        text = text.strip()
        fp = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
        cid = hashlib.md5(f"{source}|{section}|{fp}".encode("utf-8")).hexdigest()[:12]
        return Chunk(id=cid, source=source, section=section, text=text,
                     word_count=len(text.split()), fingerprint=fp)


# ---------------------------------------------------------------------------
# Markdown heading-aware chunker
# ---------------------------------------------------------------------------
_H_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _split_by_headings(md_text: str) -> list[tuple[str, str]]:
    """Split markdown by headings. Returns list of (section_path, body_text)."""
    matches = list(_H_RE.finditer(md_text))
    if not matches:
        return [("(top)", md_text)]

    sections: list[tuple[str, str]] = []
    heading_stack: list[tuple[int, str]] = []  # (level, title)

    # Preamble before first heading
    if matches[0].start() > 0:
        preamble = md_text[: matches[0].start()].strip()
        if preamble:
            sections.append(("(preamble)", preamble))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        # Update stack
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, title))
        path = " > ".join(t for _, t in heading_stack)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        body = md_text[start:end].strip()
        if body:
            sections.append((path, body))
    return sections


def _split_long_paragraph(text: str, max_words: int = _MAX_WORDS) -> list[str]:
    """Split a long block by blank lines, then greedily pack into <=max_words pieces."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buf: list[str] = []
    buf_wc = 0
    for p in paragraphs:
        wc = len(p.split())
        if buf and buf_wc + wc > max_words:
            pieces.append("\n\n".join(buf))
            buf, buf_wc = [], 0
        buf.append(p)
        buf_wc += wc
    if buf:
        pieces.append("\n\n".join(buf))
    return pieces or [text]


def chunk_markdown(md_text: str, source: str) -> list[Chunk]:
    """Turn a markdown document into a list of Chunk objects."""
    sections = _split_by_headings(md_text)
    chunks: list[Chunk] = []
    seen_fps: set[str] = set()
    for section_path, body in sections:
        for piece in _split_long_paragraph(body):
            words = piece.split()
            # Merge tiny sections with heading label so context isn't lost
            if len(words) < _MIN_WORDS and section_path not in ("(preamble)", "(top)"):
                piece = f"[{section_path}]\n{piece}"
                words = piece.split()
            if len(words) < _MIN_WORDS:
                continue
            # Further hard split if still too long
            if len(words) > _MAX_WORDS:
                for i in range(0, len(words), _MAX_WORDS):
                    sub = " ".join(words[i:i + _MAX_WORDS])
                    c = Chunk.make(source, section_path, sub)
                    if c.fingerprint not in seen_fps:
                        seen_fps.add(c.fingerprint)
                        chunks.append(c)
            else:
                c = Chunk.make(source, section_path, piece)
                if c.fingerprint not in seen_fps:
                    seen_fps.add(c.fingerprint)
                    chunks.append(c)
    return chunks


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------
def build_index(sources: Iterable[Path] | None = None,
                verbose: bool = True) -> tuple[list[Chunk], TfidfVectorizer, object]:
    """
    Read every source, chunk it, fit a TF-IDF vectorizer, persist to INDEX_DIR.
    Returns (chunks, vectorizer, matrix).
    """
    sources = list(sources) if sources is not None else DEFAULT_SOURCES
    all_chunks: list[Chunk] = []
    global_fps: set[str] = set()

    for src in sources:
        if not src.exists():
            if verbose:
                print(f"  [skip] missing: {src}")
            continue
        text = src.read_text(encoding="utf-8")
        rel = str(src.relative_to(ROOT)).replace("\\", "/")
        doc_chunks = chunk_markdown(text, rel)
        # Cross-doc dedup
        new = [c for c in doc_chunks if c.fingerprint not in global_fps]
        for c in new:
            global_fps.add(c.fingerprint)
        all_chunks.extend(new)
        if verbose:
            print(f"  [ingest] {rel}: {len(new)} chunks (from {len(doc_chunks)} pre-dedup)")

    if not all_chunks:
        raise RuntimeError("No knowledge chunks produced — check DEFAULT_SOURCES paths.")

    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.9,
        sublinear_tf=True,
        lowercase=True,
    )
    matrix = vectorizer.fit_transform([c.text for c in all_chunks])

    with open(CHUNKS_PATH, "wb") as f:
        pickle.dump(all_chunks, f)
    with open(VECTORIZER_PATH, "wb") as f:
        pickle.dump(vectorizer, f)
    with open(MATRIX_PATH, "wb") as f:
        pickle.dump(matrix, f)

    if verbose:
        print(f"\nIndex written to {INDEX_DIR}")
        print(f"  chunks:     {len(all_chunks)}")
        print(f"  vocab:      {len(vectorizer.vocabulary_)}")
        print(f"  matrix:     {matrix.shape}")
    return all_chunks, vectorizer, matrix


def load_index() -> tuple[list[Chunk], TfidfVectorizer, object]:
    """Load a previously built index. Raises FileNotFoundError if absent."""
    if not (CHUNKS_PATH.exists() and VECTORIZER_PATH.exists() and MATRIX_PATH.exists()):
        raise FileNotFoundError(
            f"RAG index not found in {INDEX_DIR}. "
            "Build it with: .venv\\Scripts\\python.exe scripts/build_rag_index.py"
        )
    with open(CHUNKS_PATH, "rb") as f:
        chunks = pickle.load(f)
    with open(VECTORIZER_PATH, "rb") as f:
        vectorizer = pickle.load(f)
    with open(MATRIX_PATH, "rb") as f:
        matrix = pickle.load(f)
    return chunks, vectorizer, matrix
