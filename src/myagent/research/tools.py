"""Five bounded, offline-testable tools for a corpus under ``data/papers``."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from myagent.config.env import MissingEnvError
from myagent.memory.manager import MemoryManager
from myagent.rag.embedder import EmbeddingError
from myagent.rag.loader import LoaderError, load_document
from myagent.rag.pipeline import RagPipeline, citation
from myagent.rag.store import StoredDocument
from myagent.rag.types import Document
from myagent.rag.vectorstore import VectorStoreError
from myagent.tools.base import Tool, ToolResult, schema_copy
from myagent.tools.registry import ToolRegistry

__all__ = [
    "ListPapersTool",
    "PaperLibrary",
    "ReadPaperTool",
    "SaveNoteTool",
    "SearchMemoryTool",
    "SearchPaperTool",
    "build_research_tools",
]

_MAX_SEARCH_TEXT = 1200
_MAX_READ_CHARS = 12000


class PaperLibrary:
    """The papers this agent may see, checked against the resolved corpus root."""

    def __init__(
        self, pipeline: RagPipeline, root: Path, *, document_ids: tuple[str, ...] | None = None
    ) -> None:
        self.pipeline = pipeline
        self.root = Path(root).expanduser().resolve()
        self.document_ids = document_ids

    def _allowed(self, source: str, document_id: str) -> bool:
        path = Path(source).expanduser().resolve()
        return (
            path.is_relative_to(self.root)
            and path.suffix.lower() == ".pdf"
            and (self.document_ids is None or document_id in self.document_ids)
        )

    def documents(self) -> list[StoredDocument]:
        """List only PDFs whose resolved source remains inside the corpus root."""
        return [
            document
            for document in self.pipeline.documents()
            if self._allowed(document.source, document.id)
        ]

    def get(self, document_id: str) -> Document | None:
        """Return an allowed document, or ``None`` for an unknown/escaped source."""
        document = self.pipeline.store.document(document_id)
        if document is None or not self._allowed(document.source, document.id):
            return None
        return document

    def ids(self) -> list[str]:
        """IDs to pass to RAG's vector filter; an empty set means no search."""
        return [document.id for document in self.documents()]


class ListPapersTool(Tool):
    """List the bounded corpus and stable IDs for subsequent tool calls."""

    name = "list_papers"
    description = "List available papers with document ID, title, page count and chunk count."
    read_only = True

    def __init__(self, library: PaperLibrary) -> None:
        self.library = library

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, **kwargs: object) -> ToolResult:
        documents = self.library.documents()
        if not documents:
            return ToolResult("知识库中没有已入库论文。")
        return ToolResult(
            "\n".join(
                f"{item.id} | {item.title or Path(item.source).stem} | "
                f"{item.pages} page(s) | {item.chunks} chunk(s)"
                for item in documents
            )
        )


_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "document_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "required": ["query"],
    "additionalProperties": False,
}


class SearchPaperTool(Tool):
    """Retrieve cited chunks, always filtered to the allowed corpus."""

    name = "search_paper"
    description = (
        "Search paper chunks; optionally restrict to document_ids. Returns cited evidence."
    )
    read_only = True

    def __init__(self, library: PaperLibrary) -> None:
        self.library = library

    @property
    def parameters(self) -> dict[str, Any]:
        return schema_copy(_SEARCH_SCHEMA)

    async def execute(
        self,
        query: str = "",
        document_ids: list[str] | None = None,
        top_k: int = 5,
        **kwargs: object,
    ) -> ToolResult:
        if not query.strip():
            return ToolResult.error("query must not be blank")
        allowed = set(self.library.ids())
        wanted = set(document_ids) if document_ids is not None else allowed
        if not wanted.issubset(allowed):
            return ToolResult.error("unknown or inaccessible document id")
        if not wanted:
            return ToolResult("知识库中没有可检索的论文。")
        try:
            hits = await self.library.pipeline.retrieve(
                query, top_k=top_k, document_ids=sorted(wanted)
            )
        except (MissingEnvError, EmbeddingError, VectorStoreError) as exc:
            return ToolResult.error(f"paper search unavailable: {exc}")
        if not hits:
            return ToolResult("知识库中没有与问题相关的内容。")
        return ToolResult(
            "\n\n".join(
                f"{citation(hit)} score={hit.score:.3f}\n{hit.chunk.text[:_MAX_SEARCH_TEXT]}"
                for hit in hits
            )
        )


_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_id": {"type": "string", "minLength": 1},
        "start_page": {"type": "integer", "minimum": 1},
        "end_page": {"type": "integer", "minimum": 1},
    },
    "required": ["document_id"],
    "additionalProperties": False,
}


class ReadPaperTool(Tool):
    """Read original PDF page text from the corpus root with citation anchors."""

    name = "read_paper"
    description = "Read a paper's original PDF text by document ID and inclusive page range."
    read_only = True

    def __init__(self, library: PaperLibrary) -> None:
        self.library = library

    @property
    def parameters(self) -> dict[str, Any]:
        return schema_copy(_READ_SCHEMA)

    async def execute(
        self,
        document_id: str = "",
        start_page: int = 1,
        end_page: int | None = None,
        **kwargs: object,
    ) -> ToolResult:
        return await asyncio.to_thread(self._read, document_id, start_page, end_page)

    def _read(self, document_id: str, start_page: int, end_page: int | None) -> ToolResult:
        """Read and verify a PDF off the event-loop thread."""
        document = self.library.get(document_id)
        if document is None:
            return ToolResult.error("unknown or inaccessible document id")
        path = Path(document.source).expanduser().resolve()
        try:
            original = load_document(path)
        except LoaderError as exc:
            return ToolResult.error(f"cannot read paper: {exc}")
        if original.metadata.get("sha256") != document.metadata.get("sha256"):
            return ToolResult.error("paper changed since ingest; ingest it again")
        spans = original.metadata.get("page_spans", [])
        last_page = len(spans)
        stop = last_page if end_page is None else end_page
        if start_page < 1 or stop < start_page or stop > last_page:
            return ToolResult.error(f"page range must be within 1..{last_page}")
        chunks = self.library.pipeline.store.chunks(document_id)
        blocks: list[str] = []
        for span in spans[start_page - 1 : stop]:
            page = int(span["page"])
            start, end = int(span["start"]), int(span["end"])
            citations = [
                f"[{document_id}#{chunk.index}]"
                for chunk in chunks
                if _overlaps(chunk.metadata.get("char_span"), start, end)
            ]
            blocks.append(f"page {page} {' '.join(citations)}\n{original.text[start:end].strip()}")
        rendered = "\n\n".join(blocks)
        if len(rendered) > _MAX_READ_CHARS:
            rendered = rendered[:_MAX_READ_CHARS] + "\n… (truncated; request fewer pages)"
        return ToolResult(rendered)


def _overlaps(raw_span: object, start: int, end: int) -> bool:
    """Whether a stored chunk's source span intersects a PDF page."""
    if not isinstance(raw_span, list) or len(raw_span) != 2:
        return False
    return int(raw_span[0]) < end and start < int(raw_span[1])


_NOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 1000},
        "importance": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["text", "importance"],
    "additionalProperties": False,
}


class SaveNoteTool(Tool):
    """Persist a meaningful conclusion through the existing MemoryManager."""

    name = "save_note"
    description = "Save an important research conclusion or durable preference as semantic memory."
    read_only = False

    def __init__(self, memory: MemoryManager) -> None:
        self.memory = memory

    @property
    def parameters(self) -> dict[str, Any]:
        return schema_copy(_NOTE_SCHEMA)

    async def execute(
        self, text: str = "", importance: float = 0.7, **kwargs: object
    ) -> ToolResult:
        if not self.memory.enabled:
            return ToolResult.error("memory is disabled; enable MYAGENT_MEMORY_ENABLED")
        if not text.strip() or not 0 <= importance <= 1:
            return ToolResult.error("text and importance must be valid")
        record = self.memory.semantic.build(text.strip(), importance=importance, source="tool")
        written = await self.memory.write([record])
        if not written:
            return ToolResult.error("note was not stored")
        return ToolResult(f"saved semantic memory {written[0].id}")


_MEMORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "required": ["query"],
    "additionalProperties": False,
}


class SearchMemoryTool(Tool):
    """Search the same MemoryManager used for automatic session context."""

    name = "search_memory"
    description = "Search saved research memories and preferences."
    read_only = True

    def __init__(self, memory: MemoryManager) -> None:
        self.memory = memory

    @property
    def parameters(self) -> dict[str, Any]:
        return schema_copy(_MEMORY_SCHEMA)

    async def execute(self, query: str = "", top_k: int = 5, **kwargs: object) -> ToolResult:
        context = await self.memory.context(query, top_k=top_k)
        if not context.hits:
            return ToolResult(context.note or "没有匹配的记忆。")
        return ToolResult(
            "\n".join(
                f"memory:{hit.record.kind}:{hit.record.id} score={hit.score:.3f} {hit.record.text}"
                for hit in context.hits
            )
        )


def build_research_tools(library: PaperLibrary, memory: MemoryManager) -> ToolRegistry:
    """Register the five application tools without editing the framework registry."""
    registry = ToolRegistry()
    for tool in (
        ListPapersTool(library),
        SearchPaperTool(library),
        ReadPaperTool(library),
        SaveNoteTool(memory),
        SearchMemoryTool(memory),
    ):
        registry.register(tool)
    return registry
