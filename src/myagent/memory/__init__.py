"""Layered memory (PLAN 4.0).

```text
MemoryManager
├── WorkingMemory     本轮对话窗口（从 SessionStore 构造，不落库）
├── EpisodicMemory    「发生过什么」，检索时按半衰期衰减
├── SemanticMemory    「我知道什么」，不衰减
└── MemoryRetriever   embed → 向量检索 → 衰减重排 → 关键词兜底
```

``myagent.memory.base`` / ``myagent.memory.types`` stay dependency-free (they are
the Phase 3 contract the AST test in ``tests/test_contracts.py`` pins); the
concrete pieces below arrive with the module. :func:`myagent.runtime.build_agent`
is the only place where they are assembled, exactly like every other component.
"""

from myagent.memory.base import BaseMemory
from myagent.memory.consolidator import ConsolidationResult, Consolidator
from myagent.memory.embedder import EmbeddingError, OpenAICompatEmbedder
from myagent.memory.episodic import EpisodicMemory
from myagent.memory.extractor import MemoryExtractor, Turn
from myagent.memory.manager import MemoryManager
from myagent.memory.retriever import MemoryRetriever
from myagent.memory.semantic import SemanticMemory
from myagent.memory.sqlite_store import SQLiteMemoryStore
from myagent.memory.types import (
    EPISODIC,
    KINDS,
    SEMANTIC,
    SOURCES,
    MemoryContext,
    MemoryHit,
    MemoryRecord,
)
from myagent.memory.vector_index import (
    MemoryIndex,
    MemoryIndexError,
    QdrantMemoryIndex,
    VectorHit,
)
from myagent.memory.working import WorkingMemory

__all__ = [
    "EPISODIC",
    "KINDS",
    "SEMANTIC",
    "SOURCES",
    "BaseMemory",
    "ConsolidationResult",
    "Consolidator",
    "EmbeddingError",
    "EpisodicMemory",
    "MemoryContext",
    "MemoryExtractor",
    "MemoryHit",
    "MemoryIndex",
    "MemoryIndexError",
    "MemoryManager",
    "MemoryRecord",
    "MemoryRetriever",
    "OpenAICompatEmbedder",
    "QdrantMemoryIndex",
    "SQLiteMemoryStore",
    "SemanticMemory",
    "Turn",
    "VectorHit",
    "WorkingMemory",
]
