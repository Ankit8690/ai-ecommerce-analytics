"""Phase 7 RAG package — knowledge retrieval over project documentation."""
from rag.retriever import Retriever, RetrievalResult
from rag.ingest import Chunk, build_index, DEFAULT_SOURCES, INDEX_DIR

__all__ = ["Retriever", "RetrievalResult", "Chunk", "build_index",
           "DEFAULT_SOURCES", "INDEX_DIR"]
