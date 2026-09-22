"""``RagPipeline``: the one door into the RAG system (PLAN 5.0 / 5.8).

```text
ingest:  Document → Loader → Chunker → Embedder → VectorStore   (idempotent)
query:   Query → Embedder → VectorStore.search → (Reranker) → RetrievedChunk[]
```

Like ``MemoryManager`` for memory, this is a *facade*: the CLI, the future
``search_paper`` tool and the experiments all go through it, so a document can
only be ingested one way (load → chunk → embed → store, in that order) and only
be read one way (retriever → optional reranker).

Phase 6 adds the second reader: :meth:`RagPipeline.recall` is the agent-facing
``DocumentProvider`` (``src/myagent/agent/context.py``) and returns neutral
context items, so the context manager can trim chunks by score without importing
anything from this package. ``MYAGENT_RAG_ENABLED=false`` turns it into an empty
recall — the switch Phase 8's ON/OFF comparison uses.

The ingest path in detail, because the order is the whole design:

1. every file is loaded and chunked *before* any network call, so a typo in the
   fifth path fails before the first embed;
2. the chunks of all files are embedded in one batch stream, which is where the
   embedding dimension becomes known (``len(vector)``);
3. that dimension is checked against ``EMBED_DIM`` and — when the variable was
   empty — written back to ``.env`` (PLAN 5.4). The Qdrant collection is created
   for it and can never disagree with it again;
4. only then are rows and points written: same ``document_id`` for the same
   content, same derived point ids, same chunk ids, so re-ingesting a file
   updates it instead of duplicating it (PLAN 5.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from myagent.agent.context import ContextItem
from myagent.config.env import remember_env
from myagent.config.settings import ENV_EMBED_DIM, EmbeddingSettings, RagSettings
from myagent.observability.logging import get_logger
from myagent.rag.chunker import BaseChunker, FixedSizeChunker
from myagent.rag.embedder import BaseEmbedder, EmbeddingError
from myagent.rag.loader import BaseLoader, load_document
from myagent.rag.reranker import BaseReranker, IdentityReranker
from myagent.rag.retriever import VectorRetriever
from myagent.rag.store import SQLiteDocumentStore, StoredDocument
from myagent.rag.types import Chunk, Document, RetrievedChunk
from myagent.rag.vectorstore import BaseVectorStore

__all__ = [
    "IngestReport",
    "IngestedDocument",
    "RagPipeline",
    "citation",
    "citation_label",
]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IngestedDocument:
    """What happened to one file during an ingest."""

    document_id: str
    source: str
    title: str | None
    chunks: int
    created: bool


@dataclass(frozen=True, slots=True)
class IngestReport:
    """The result of one ``ingest`` call, ready to be printed by the CLI."""

    documents: tuple[IngestedDocument, ...] = ()
    dim: int | None = None
    dim_probed: bool = False

    @property
    def added(self) -> int:
        """How many files were new to the store."""
        return sum(1 for document in self.documents if document.created)

    @property
    def updated(self) -> int:
        """How many files were already stored (re-ingested)."""
        return len(self.documents) - self.added

    @property
    def chunk_count(self) -> int:
        """How many chunks were written across all files."""
        return sum(document.chunks for document in self.documents)

    @property
    def document_ids(self) -> tuple[str, ...]:
        """The ids of the ingested documents, in the order they were given."""
        return tuple(document.document_id for document in self.documents)


def citation(hit: RetrievedChunk) -> str:
    """The ``[document_id#index]`` reference of PLAN 5.8, plus where it came from.

    The bracketed part is the stable half: it is what an answer quotes and what a
    reader can search for in ``myagent docs list``. The rest is the human half.
    """
    return f"[{hit.document.id}#{hit.chunk.index}] {_citation_where(hit)}"


def citation_label(hit: RetrievedChunk) -> str:
    """The same citation without the brackets around its id.

    Two consumers want the same identity in two shapes: the CLI prints
    :func:`citation` so a reader can quote it, and
    :class:`~myagent.agent.context.ContextItem` carries this one because the
    context renderer adds the brackets itself.
    """
    return f"{hit.document.id}#{hit.chunk.index} {_citation_where(hit)}"


def _citation_where(hit: RetrievedChunk) -> str:
    """The human half of a citation: which document, and which page it came from."""
    page = hit.chunk.metadata.get("page")
    where = f"page {page}" if page is not None else "no page"
    return f"{hit.document.title or hit.document.source} ({where})"


class RagPipeline:
    """Ingest files into the knowledge base and retrieve chunks back out of it."""

    def __init__(
        self,
        store: SQLiteDocumentStore,
        embedder: BaseEmbedder,
        vectorstore: BaseVectorStore,
        *,
        chunker: BaseChunker | None = None,
        loaders: Sequence[BaseLoader] | None = None,
        reranker: BaseReranker | None = None,
        settings: RagSettings | None = None,
        embedding: EmbeddingSettings | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._vectorstore = vectorstore
        self._settings = settings if settings is not None else RagSettings()
        self._embedding = embedding if embedding is not None else EmbeddingSettings()
        self._chunker = (
            chunker
            if chunker is not None
            else FixedSizeChunker(self._settings.chunk_size, self._settings.chunk_overlap)
        )
        self._loaders = loaders
        self.retriever = VectorRetriever(store, embedder, vectorstore, settings=self._settings)
        self.reranker = reranker if reranker is not None else IdentityReranker()

    @property
    def settings(self) -> RagSettings:
        """The chunking and retrieval settings in force."""
        return self._settings

    @property
    def store(self) -> SQLiteDocumentStore:
        """The document store (SQLite)."""
        return self._store

    # --- ingest (PLAN 5.8) -------------------------------------------------

    async def ingest(self, paths: Sequence[Path]) -> IngestReport:
        """Ingest ``paths`` (idempotently) and report what happened."""
        loaded: list[tuple[Document, list[Chunk]]] = []
        for raw in paths:
            document = load_document(Path(raw), self._loaders)
            loaded.append((document, self._chunker.split(document)))
        texts = [chunk.text for _, chunks in loaded for chunk in chunks]
        if not texts:
            # An empty document (no text layer) is still worth storing: it shows up
            # in `docs list`, and there is nothing to embed or to search.
            empty = [self._write(document, chunks, []) for document, chunks in loaded]
            return IngestReport(documents=tuple(empty))
        vectors = await self._embeddings(texts)
        dim = len(vectors[0])
        self._check_dim(dim)
        probed = self._remember_dim(dim)
        self._vectorstore.ensure_collection(dim)
        written: list[IngestedDocument] = []
        offset = 0
        for document, chunks in loaded:
            written.append(self._write(document, chunks, vectors[offset : offset + len(chunks)]))
            offset += len(chunks)
        return IngestReport(documents=tuple(written), dim=dim, dim_probed=probed)

    def _write(
        self, document: Document, chunks: list[Chunk], vectors: list[list[float]]
    ) -> IngestedDocument:
        """Write one document's rows and points; rows are the source of truth."""
        created = self._store.put_document(document)
        self._store.replace_chunks(document.id, chunks)
        if chunks:
            self._vectorstore.upsert(chunks, vectors)
        return IngestedDocument(
            document_id=document.id,
            source=document.source,
            title=document.title,
            chunks=len(chunks),
            created=created,
        )

    async def _embeddings(self, texts: list[str]) -> list[list[float]]:
        """Embed every chunk text in one stream, keeping the order."""
        vectors = await self._embedder.embed(texts)
        if not vectors:  # an embedder that answers nothing cannot fill a collection
            raise EmbeddingError(
                f"the embedder returned no vectors for {len(texts)} chunk(s); nothing was written"
            )
        return vectors

    def _check_dim(self, dim: int) -> None:
        """Refuse to build a collection the configured ``EMBED_DIM`` disagrees with."""
        configured = self._embedding.dim
        if configured is not None and configured != dim:
            raise EmbeddingError(
                f"EMBED_DIM is {configured} but the embedding model "
                f"{self._embedding.model_name!r} returned {dim}-dimensional vectors; "
                "fix EMBED_DIM (or EMBED_MODEL_NAME) and re-run the ingest"
            )

    def _remember_dim(self, dim: int) -> bool:
        """Record a probed dimension in ``.env`` so later runs agree (PLAN 5.4)."""
        if self._embedding.dim is not None:
            return False
        path = remember_env(ENV_EMBED_DIM, str(dim))
        logger.info("probed embedding dimension %d and recorded it in %s", dim, path)
        return True

    # --- query (PLAN 5.6 / 5.7) --------------------------------------------

    async def retrieve(
        self, query: str, top_k: int | None = None, document_ids: list[str] | None = None
    ) -> list[RetrievedChunk]:
        """Retrieve (and re-rank) the chunks that answer ``query``."""
        limit = self._settings.top_k if top_k is None else top_k
        hits = await self.retriever.retrieve(query, limit, document_ids=document_ids)
        return await self.reranker.rerank(query, hits, limit)

    async def recall(self, query: str, *, top_k: int | None = None) -> list[ContextItem]:
        """The agent-facing recall of PLAN 6.5: citable context items, best first.

        Same retrieval path as :meth:`retrieve` (embed → search → rerank), one
        layer of presentation less: each item is the chunk's text plus the
        citation as its reference and the score the budget trims by. An empty
        answer is not an error — it means "nothing to add this turn", which is
        also what ``MYAGENT_RAG_ENABLED=false`` answers, deliberately.
        """
        if not self._settings.enabled:
            logger.debug("rag is disabled (MYAGENT_RAG_ENABLED); recall returns nothing")
            return []
        hits = await self.retrieve(query, top_k)
        return [
            ContextItem(
                text=hit.chunk.text.strip(),
                reference=citation_label(hit),
                score=hit.score,
            )
            for hit in hits
        ]

    def build_context(self, chunks: Sequence[RetrievedChunk]) -> str:
        """Render retrieved chunks as citable context for a prompt (PLAN 5.8).

        Each block starts with ``[document_id#index]`` so the model can quote a
        source and a reader can jump back to it, followed by the title, the page
        and the chunk text. The format is deliberately boring: Phase 6 measures
        how much of the context budget it costs, and a stable shape means the
        measurement stays comparable.
        """
        return "\n\n".join(f"{citation(hit)}\n{hit.chunk.text.strip()}" for hit in chunks)

    # --- maintenance (PLAN 5.8 ``myagent docs ...``) -----------------------

    def documents(self) -> list[StoredDocument]:
        """Every stored document with its chunk count, newest first."""
        return self._store.documents()

    def delete(self, document_id: str) -> bool:
        """Forget one document: its points first, then its rows (cascading chunks).

        Returns ``False`` when the id is unknown, so the CLI can answer with an
        error instead of pretending something was deleted.
        """
        if self._store.document(document_id) is None:
            return False
        self._vectorstore.delete_document(document_id)
        return self._store.delete_document(document_id)
