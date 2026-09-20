"""RAG contracts (Phase 3) and pipeline (Phase 5).

Only the interfaces and types exist today: Phase 3 pins the extension points so
``build_agent()`` has something stable to assemble against, and Phase 5 adds the
loader / chunker / embedder / store / retriever implementations behind them.
"""

from myagent.rag.embedder import BaseEmbedder
from myagent.rag.retriever import BaseRetriever
from myagent.rag.types import Chunk, Document, Filter, RetrievedChunk, ScoredPoint
from myagent.rag.vectorstore import BaseVectorStore

__all__ = [
    "BaseEmbedder",
    "BaseRetriever",
    "BaseVectorStore",
    "Chunk",
    "Document",
    "Filter",
    "RetrievedChunk",
    "ScoredPoint",
]
