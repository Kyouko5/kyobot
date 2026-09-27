"""A two-PDF corpus with real SQLite rows and in-process vector doubles."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from fakes import BagOfWordsEmbedder, DictionaryIndex, DictionaryVectorStore
from myagent.config.settings import (
    AgentSettings,
    EmbeddingSettings,
    LLMSettings,
    MemorySettings,
    QdrantSettings,
    RagSettings,
    Settings,
    SQLiteSettings,
)
from myagent.memory.manager import MemoryManager
from myagent.memory.sqlite_store import SQLiteMemoryStore
from myagent.rag.pipeline import RagPipeline
from myagent.rag.store import SQLiteDocumentStore
from myagent.research.tools import PaperLibrary
from myagent.session.manager import JsonlSessionStore

FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "research_agent" / "fixtures"


@dataclass
class Corpus:
    settings: Settings
    root: Path
    rag: RagPipeline
    memory: MemoryManager
    library: PaperLibrary
    ids: tuple[str, str]


@pytest.fixture
async def corpus(tmp_path: Path) -> Corpus:
    root = tmp_path / "papers"
    root.mkdir()
    paths = []
    for name in ("graph_indexing.pdf", "dense_retrieval.pdf"):
        target = root / name
        shutil.copyfile(FIXTURES / name, target)
        paths.append(target)
    settings = Settings(
        llm=LLMSettings(model="offline-model", api_key="offline-key"),
        agent=AgentSettings(workspace=tmp_path, sessions_dir=tmp_path / "sessions"),
        sqlite=SQLiteSettings(tmp_path / "research.db"),
        qdrant=QdrantSettings(),
        embedding=EmbeddingSettings(api_key="offline-key"),
        memory=MemorySettings(enabled=True),
        rag=RagSettings(enabled=True, chunk_size=180, chunk_overlap=0),
    )
    embedder = BagOfWordsEmbedder()
    rag = RagPipeline(
        SQLiteDocumentStore(settings.sqlite),
        embedder,
        DictionaryVectorStore(),
        settings=settings.rag,
        embedding=settings.embedding,
    )
    report = await rag.ingest(paths)
    memory = MemoryManager(
        SQLiteMemoryStore(settings.sqlite),
        DictionaryIndex(),
        embedder,
        collection="myagent_memories",
        embedding_model="fake-embed",
        sessions=JsonlSessionStore.from_settings(settings.agent),
        settings=settings.memory,
    )
    return Corpus(settings, root, rag, memory, PaperLibrary(rag, root), report.document_ids)
