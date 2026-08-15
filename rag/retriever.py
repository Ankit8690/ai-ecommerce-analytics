"""
RAG retrieval — query-time interface over a persisted TF-IDF index.

Designed so the embedding backend can be swapped later (e.g. Gemini
`text-embedding-004`) without changing the retrieval contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sklearn.metrics.pairwise import cosine_similarity

from rag.ingest import Chunk, load_index


@dataclass
class RetrievalResult:
    """One retrieved chunk plus its similarity score."""
    chunk: Chunk
    score: float

    def citation(self) -> str:
        """Short human-readable citation string."""
        return f"{self.chunk.source} § {self.chunk.section}"


class Retriever:
    """Cosine-similarity retriever over the TF-IDF chunk index."""

    def __init__(self, min_score: float = 0.05) -> None:
        self.chunks, self.vectorizer, self.matrix = load_index()
        self.min_score = min_score

    def retrieve(self, query: str, k: int = 5,
                 min_score: Optional[float] = None) -> list[RetrievalResult]:
        """Return top-k chunks with cosine similarity >= min_score."""
        if not query or not query.strip():
            return []
        threshold = self.min_score if min_score is None else min_score
        q_vec = self.vectorizer.transform([query])
        sims = cosine_similarity(q_vec, self.matrix)[0]
        # top-k indices by score
        order = sims.argsort()[::-1]
        out: list[RetrievalResult] = []
        for idx in order[: k * 2]:  # slight overfetch for threshold filtering
            s = float(sims[idx])
            if s < threshold:
                break
            out.append(RetrievalResult(chunk=self.chunks[int(idx)], score=s))
            if len(out) >= k:
                break
        return out

    def stats(self) -> dict:
        """Basic index diagnostics."""
        return {
            "chunk_count": len(self.chunks),
            "vocab_size": len(self.vectorizer.vocabulary_),
            "matrix_shape": tuple(self.matrix.shape),
            "sources": sorted({c.source for c in self.chunks}),
        }
