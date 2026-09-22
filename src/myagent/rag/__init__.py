"""The RAG system: contracts (Phase 3) and the pipeline behind them (Phase 5).

```text
ingest:  Document → Loader → Chunker → Embedder → VectorStore   (idempotent)
query:   Query → Embedder → VectorStore.search → (Reranker) → RetrievedChunk[]
```

The three Phase 3 contracts (``BaseEmbedder`` / ``BaseVectorStore`` /
``BaseRetriever``) are what the rest of the framework type-checks against; the
implementations below them are what ``myagent.runtime.build_rag()`` assembles.
Only :mod:`myagent.rag.vectorstore` and :mod:`myagent.rag.embedder` touch a
provider SDK — the same boundary the model layer drew in Phase 3, and the reason
``tests/rag`` runs end to end without a server or a credential.
"""

from myagent.rag.chunker import BaseChunker, FixedSizeChunker
from myagent.rag.embedder import (
    BaseEmbedder,
    DashScopeEmbedder,
    EmbeddingError,
    OpenAICompatEmbedder,
    OpenAIEmbedder,
    build_embedder,
    normalize_vector,
)
from myagent.rag.loader import (
    BaseLoader,
    LoaderError,
    MarkdownLoader,
    PdfLoader,
    TextLoader,
    UnreadableDocumentError,
    UnsupportedFormatError,
    load_document,
)
from myagent.rag.pipeline import IngestedDocument, IngestReport, RagPipeline, citation
from myagent.rag.reranker import BaseReranker, IdentityReranker, ScoreReranker
from myagent.rag.retriever import BaseRetriever, VectorRetriever
from myagent.rag.store import SQLiteDocumentStore, StoredDocument
from myagent.rag.types import (
    Chunk,
    Document,
    Filter,
    RetrievedChunk,
    ScoredPoint,
    chunk_id,
    content_id,
    heading_at,
    page_at,
)
from myagent.rag.vectorstore import (
    BaseVectorStore,
    QdrantVectorStore,
    VectorStoreError,
)

__all__ = [
    "BaseChunker",
    "BaseEmbedder",
    "BaseLoader",
    "BaseReranker",
    "BaseRetriever",
    "BaseVectorStore",
    "Chunk",
    "DashScopeEmbedder",
    "Document",
    "EmbeddingError",
    "Filter",
    "FixedSizeChunker",
    "IdentityReranker",
    "IngestReport",
    "IngestedDocument",
    "LoaderError",
    "MarkdownLoader",
    "OpenAICompatEmbedder",
    "OpenAIEmbedder",
    "PdfLoader",
    "QdrantVectorStore",
    "RagPipeline",
    "RetrievedChunk",
    "SQLiteDocumentStore",
    "ScoreReranker",
    "ScoredPoint",
    "StoredDocument",
    "TextLoader",
    "UnreadableDocumentError",
    "UnsupportedFormatError",
    "VectorRetriever",
    "VectorStoreError",
    "build_embedder",
    "chunk_id",
    "citation",
    "content_id",
    "heading_at",
    "load_document",
    "normalize_vector",
    "page_at",
]
