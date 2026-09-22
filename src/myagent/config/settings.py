"""Typed settings for the storage and embedding layers.

Decisions:

* docs/decision-records/0003-storage-and-vector-store.md — documents and metadata
  live in SQLite, vectors live in Qdrant.
* docs/decision-records/0005-embedding-provider.md — embeddings come from Aliyun
  DashScope (``EMBED_MODEL_TYPE=dashscope``).

These dataclasses are the single place where environment variables turn into
usable settings; the concrete stores and embedders (Phase 4/5) accept them
instead of reading the environment themselves.

Naming convention: credential variables use the short ``LLM_*`` / ``EMBED_*``
form, project-level switches use the ``MYAGENT_*`` prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from myagent.config.env import MissingEnvError, get_bool_env, get_env, load_env

ENV_SQLITE_PATH: Final = "MYAGENT_SQLITE_PATH"
ENV_QDRANT_URL: Final = "MYAGENT_QDRANT_URL"
ENV_QDRANT_API_KEY: Final = "MYAGENT_QDRANT_API_KEY"
ENV_QDRANT_COLLECTION: Final = "MYAGENT_QDRANT_COLLECTION"
ENV_QDRANT_MEMORY_COLLECTION: Final = "MYAGENT_QDRANT_MEMORY_COLLECTION"
ENV_QDRANT_PREFER_GRPC: Final = "MYAGENT_QDRANT_PREFER_GRPC"

DEFAULT_SQLITE_PATH: Final = Path("data/myagent.db")
DEFAULT_QDRANT_URL: Final = "http://localhost:6333"
DEFAULT_QDRANT_COLLECTION: Final = "myagent_documents"
# ADR-0008: memory vectors live in their own collection. Memory and documents
# have different lifecycles (a memory can be forgotten by id, a document is
# re-ingested or deleted whole), so sharing one collection would let a document
# cleanup delete memory points.
DEFAULT_QDRANT_MEMORY_COLLECTION: Final = "myagent_memories"

_COLLECTION_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

ENV_EMBED_MODEL_TYPE: Final = "EMBED_MODEL_TYPE"
ENV_EMBED_MODEL_NAME: Final = "EMBED_MODEL_NAME"
ENV_EMBED_API_KEY: Final = "EMBED_API_KEY"
ENV_EMBED_BASE_URL: Final = "EMBED_BASE_URL"
ENV_EMBED_DIM: Final = "EMBED_DIM"
ENV_EMBED_BATCH_SIZE: Final = "EMBED_BATCH_SIZE"

SUPPORTED_EMBED_MODEL_TYPES: Final = ("dashscope", "openai")
DEFAULT_EMBED_MODEL_TYPE: Final = "dashscope"
DEFAULT_EMBED_MODEL_NAME: Final = "qwen3.7-text-embedding-flash"
# The endpoints reject oversized batches, and a document ingest is not a turn's
# two or three memories: PLAN 5.4 fixes the default at 16 texts per request.
DEFAULT_EMBED_BATCH_SIZE: Final = 16

_FALLBACK_API_KEY_VARS: Final = {
    "dashscope": "DASHSCOPE_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_DEFAULT_EMBED_BASE_URLS: Final = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}

_LLM_FALLBACK_API_KEY_VARS: Final = {
    "openai_compat": "OPENAI_API_KEY",
}

_DEFAULT_LLM_BASE_URLS: Final = {
    "openai_compat": "https://api.openai.com/v1",
}

# --- Phase 2: model provider and agent runtime -------------------------------

ENV_LLM_PROVIDER: Final = "LLM_PROVIDER"
ENV_LLM_MODEL: Final = "LLM_MODEL"
ENV_LLM_API_KEY: Final = "LLM_API_KEY"
ENV_LLM_BASE_URL: Final = "LLM_BASE_URL"
ENV_LLM_MAX_TOKENS: Final = "LLM_MAX_TOKENS"
ENV_LLM_CONTEXT_WINDOW: Final = "LLM_CONTEXT_WINDOW"
ENV_LLM_TEMPERATURE: Final = "LLM_TEMPERATURE"

SUPPORTED_LLM_PROVIDERS: Final = ("openai_compat",)
DEFAULT_LLM_PROVIDER: Final = "openai_compat"
DEFAULT_LLM_MAX_TOKENS: Final = 4096
DEFAULT_LLM_CONTEXT_WINDOW: Final = 128_000
DEFAULT_LLM_TEMPERATURE: Final = 0.1

ENV_AGENT_MAX_ITERATIONS: Final = "AGENT_MAX_ITERATIONS"
ENV_AGENT_TOOL_TIMEOUT_S: Final = "AGENT_TOOL_TIMEOUT_S"
ENV_AGENT_MAX_TOOL_RESULT_CHARS: Final = "AGENT_MAX_TOOL_RESULT_CHARS"
ENV_AGENT_WORKSPACE: Final = "AGENT_WORKSPACE"
ENV_AGENT_SESSIONS_DIR: Final = "AGENT_SESSIONS_DIR"

DEFAULT_AGENT_MAX_ITERATIONS: Final = 12
DEFAULT_AGENT_TOOL_TIMEOUT_S: Final = 30.0
DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS: Final = 8_000
DEFAULT_AGENT_WORKSPACE: Final = Path("workspace")
DEFAULT_AGENT_SESSIONS_DIR: Final = Path("data/sessions")

# --- Phase 4: layered memory -------------------------------------------------

ENV_MEMORY_ENABLED: Final = "MYAGENT_MEMORY_ENABLED"
ENV_MEMORY_HALF_LIFE_DAYS: Final = "MYAGENT_MEMORY_HALF_LIFE_DAYS"
ENV_MEMORY_TOP_K: Final = "MYAGENT_MEMORY_TOP_K"
ENV_MEMORY_MAX_TEXT_CHARS: Final = "MYAGENT_MEMORY_MAX_TEXT_CHARS"
ENV_MEMORY_MAX_RECORDS_PER_TURN: Final = "MYAGENT_MEMORY_MAX_RECORDS_PER_TURN"

DEFAULT_MEMORY_ENABLED: Final = True
DEFAULT_MEMORY_HALF_LIFE_DAYS: Final = 30.0
DEFAULT_MEMORY_TOP_K: Final = 5
DEFAULT_MEMORY_MAX_TEXT_CHARS: Final = 500
DEFAULT_MEMORY_MAX_RECORDS_PER_TURN: Final = 3
# Must stay equal to ``myagent.memory.types.DEFAULT_IMPORTANCE``: the extractor
# drops anything below it (PLAN 4.6), and tests/test_settings.py pins that.
DEFAULT_MEMORY_MIN_IMPORTANCE: Final = 0.5
DEFAULT_MEMORY_DEDUP_THRESHOLD: Final = 0.95
DEFAULT_MEMORY_DEDUP_RECENT: Final = 8
DEFAULT_MEMORY_SHORT_QUERY_CHARS: Final = 8

# --- Phase 5: RAG ------------------------------------------------------------

ENV_RAG_ENABLED: Final = "MYAGENT_RAG_ENABLED"
ENV_RAG_CHUNK_SIZE: Final = "MYAGENT_RAG_CHUNK_SIZE"
ENV_RAG_CHUNK_OVERLAP: Final = "MYAGENT_RAG_CHUNK_OVERLAP"
ENV_RAG_TOP_K: Final = "MYAGENT_RAG_TOP_K"

# ADR-0009 picks these from the chunk-size experiment in
# docs/records/phase-5-rag.md: 800 characters of mixed Chinese/English text is
# roughly 300–400 tokens, which fits a citation into a context budget without
# cutting a paragraph in half, and 120 characters of overlap keeps the sentence
# that straddles a boundary retrievable from both sides.
DEFAULT_RAG_ENABLED: Final = True
DEFAULT_RAG_CHUNK_SIZE: Final = 800
DEFAULT_RAG_CHUNK_OVERLAP: Final = 120
DEFAULT_RAG_TOP_K: Final = 5


@dataclass(frozen=True, slots=True)
class SQLiteSettings:
    """Where documents, chunks and metadata are stored."""

    path: Path = DEFAULT_SQLITE_PATH

    @classmethod
    def from_env(cls) -> SQLiteSettings:
        """Build settings from the environment, loading ``.env`` first."""
        load_env()
        raw = get_env(ENV_SQLITE_PATH)
        return cls(path=Path(raw).expanduser() if raw else DEFAULT_SQLITE_PATH)


@dataclass(frozen=True, slots=True)
class QdrantSettings:
    """How to reach the Qdrant instance holding the vector indexes.

    Two collections, one client: ``collection`` holds document chunks (Phase 5)
    and ``memory_collection`` holds memory records (Phase 4, ADR-0008).
    """

    url: str = DEFAULT_QDRANT_URL
    collection: str = DEFAULT_QDRANT_COLLECTION
    memory_collection: str = DEFAULT_QDRANT_MEMORY_COLLECTION
    api_key: str | None = None
    prefer_grpc: bool = False

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"Qdrant url must start with http:// or https://, got {self.url!r}")
        for name, value in (
            ("collection", self.collection),
            ("memory_collection", self.memory_collection),
        ):
            if not _COLLECTION_PATTERN.match(value):
                raise ValueError(f"invalid Qdrant {name} name: {value!r}")
        if self.collection == self.memory_collection:
            raise ValueError(
                "Qdrant document and memory collections must differ "
                f"(both are {self.collection!r}); see ADR-0008"
            )

    @classmethod
    def from_env(cls) -> QdrantSettings:
        """Build settings from the environment, loading ``.env`` first.

        A local Qdrant needs no API key, so ``api_key`` stays ``None`` when the
        variable is unset or left blank.
        """
        load_env()
        return cls(
            url=get_env(ENV_QDRANT_URL, DEFAULT_QDRANT_URL) or DEFAULT_QDRANT_URL,
            collection=get_env(ENV_QDRANT_COLLECTION, DEFAULT_QDRANT_COLLECTION)
            or DEFAULT_QDRANT_COLLECTION,
            memory_collection=get_env(
                ENV_QDRANT_MEMORY_COLLECTION, DEFAULT_QDRANT_MEMORY_COLLECTION
            )
            or DEFAULT_QDRANT_MEMORY_COLLECTION,
            api_key=get_env(ENV_QDRANT_API_KEY),
            prefer_grpc=get_bool_env(ENV_QDRANT_PREFER_GRPC),
        )

    def client_kwargs(self) -> dict[str, object]:
        """Keyword arguments for ``qdrant_client.QdrantClient`` (Phase 5)."""
        kwargs: dict[str, object] = {"url": self.url, "prefer_grpc": self.prefer_grpc}
        if self.api_key is not None:
            kwargs["api_key"] = self.api_key
        return kwargs


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    """Which embedding service to call and how to authenticate."""

    model_type: str = DEFAULT_EMBED_MODEL_TYPE
    model_name: str = DEFAULT_EMBED_MODEL_NAME
    api_key: str | None = None
    base_url: str | None = None
    dim: int | None = None
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE

    def __post_init__(self) -> None:
        if self.model_type not in SUPPORTED_EMBED_MODEL_TYPES:
            supported = ", ".join(SUPPORTED_EMBED_MODEL_TYPES)
            raise ValueError(
                f"unsupported embedding model type {self.model_type!r}; expected one of {supported}"
            )
        if not self.model_name.strip():
            raise ValueError("EMBED_MODEL_NAME must not be empty")
        if self.base_url is not None and not self.base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"EMBED_BASE_URL must start with http:// or https://, got {self.base_url!r}"
            )
        if self.dim is not None and self.dim <= 0:
            raise ValueError(f"EMBED_DIM must be a positive integer, got {self.dim}")
        if self.batch_size <= 0:
            raise ValueError(f"{ENV_EMBED_BATCH_SIZE} must be positive, got {self.batch_size}")

    @classmethod
    def from_env(cls) -> EmbeddingSettings:
        """Build settings from the environment, loading ``.env`` first.

        ``EMBED_API_KEY`` wins; when it is blank the provider's usual variable is
        used instead (``DASHSCOPE_API_KEY`` for DashScope, ``OPENAI_API_KEY`` for
        OpenAI), so an existing shell export keeps working.
        """
        load_env()
        model_type = (
            get_env(ENV_EMBED_MODEL_TYPE, DEFAULT_EMBED_MODEL_TYPE) or DEFAULT_EMBED_MODEL_TYPE
        ).lower()
        fallback_var = _FALLBACK_API_KEY_VARS.get(model_type)
        api_key = get_env(ENV_EMBED_API_KEY) or (get_env(fallback_var) if fallback_var else None)
        return cls(
            model_type=model_type,
            model_name=get_env(ENV_EMBED_MODEL_NAME, DEFAULT_EMBED_MODEL_NAME)
            or DEFAULT_EMBED_MODEL_NAME,
            api_key=api_key,
            base_url=get_env(ENV_EMBED_BASE_URL),
            dim=_parse_dim(get_env(ENV_EMBED_DIM)),
            batch_size=_parse_positive_int(
                ENV_EMBED_BATCH_SIZE,
                get_env(ENV_EMBED_BATCH_SIZE),
                DEFAULT_EMBED_BATCH_SIZE,
            ),
        )

    def resolved_base_url(self) -> str | None:
        """Explicit ``EMBED_BASE_URL``, otherwise the provider's OpenAI-compatible endpoint."""
        return self.base_url or _DEFAULT_EMBED_BASE_URLS.get(self.model_type)

    def require_api_key(self) -> str:
        """Return the embedding API key, raising :class:`MissingEnvError` if unset."""
        if self.api_key:
            return self.api_key
        raise MissingEnvError(ENV_EMBED_API_KEY)


@dataclass(frozen=True, slots=True)
class MemorySettings:
    """How the layered memory behaves (PLAN 4.0–4.7).

    ``enabled`` is the Phase 8 switch: with ``MYAGENT_MEMORY_ENABLED=false`` the
    loop still runs, it simply neither recalls nor writes. The rest are the
    write policy of PLAN 4.6 (importance floor, per-turn caps), the decay law of
    PLAN 4.3 and the retrieval knobs of PLAN 4.7.
    """

    enabled: bool = DEFAULT_MEMORY_ENABLED
    half_life_days: float = DEFAULT_MEMORY_HALF_LIFE_DAYS
    top_k: int = DEFAULT_MEMORY_TOP_K
    max_text_chars: int = DEFAULT_MEMORY_MAX_TEXT_CHARS
    max_records_per_turn: int = DEFAULT_MEMORY_MAX_RECORDS_PER_TURN
    min_importance: float = DEFAULT_MEMORY_MIN_IMPORTANCE
    dedup_threshold: float = DEFAULT_MEMORY_DEDUP_THRESHOLD
    dedup_recent: int = DEFAULT_MEMORY_DEDUP_RECENT
    short_query_chars: int = DEFAULT_MEMORY_SHORT_QUERY_CHARS

    def __post_init__(self) -> None:
        if self.half_life_days <= 0:
            raise ValueError(
                f"{ENV_MEMORY_HALF_LIFE_DAYS} must be positive, got {self.half_life_days}"
            )
        if self.top_k <= 0:
            raise ValueError(f"{ENV_MEMORY_TOP_K} must be positive, got {self.top_k}")
        if self.max_text_chars <= 0:
            raise ValueError(
                f"{ENV_MEMORY_MAX_TEXT_CHARS} must be positive, got {self.max_text_chars}"
            )
        if self.max_records_per_turn <= 0:
            raise ValueError(
                f"{ENV_MEMORY_MAX_RECORDS_PER_TURN} must be positive, "
                f"got {self.max_records_per_turn}"
            )
        if not 0.0 <= self.min_importance <= 1.0:
            raise ValueError(f"min_importance must be within 0..1, got {self.min_importance}")
        if not 0.0 <= self.dedup_threshold <= 1.0:
            raise ValueError(f"dedup_threshold must be within 0..1, got {self.dedup_threshold}")
        if self.dedup_recent < 0:
            raise ValueError(f"dedup_recent must not be negative, got {self.dedup_recent}")
        if self.short_query_chars < 0:
            raise ValueError(
                f"short_query_chars must not be negative, got {self.short_query_chars}"
            )

    @classmethod
    def from_env(cls) -> MemorySettings:
        """Build settings from the environment, loading ``.env`` first."""
        load_env()
        return cls(
            enabled=get_bool_env(ENV_MEMORY_ENABLED, DEFAULT_MEMORY_ENABLED),
            half_life_days=_parse_positive_float(
                ENV_MEMORY_HALF_LIFE_DAYS,
                get_env(ENV_MEMORY_HALF_LIFE_DAYS),
                DEFAULT_MEMORY_HALF_LIFE_DAYS,
            ),
            top_k=_parse_positive_int(
                ENV_MEMORY_TOP_K, get_env(ENV_MEMORY_TOP_K), DEFAULT_MEMORY_TOP_K
            ),
            max_text_chars=_parse_positive_int(
                ENV_MEMORY_MAX_TEXT_CHARS,
                get_env(ENV_MEMORY_MAX_TEXT_CHARS),
                DEFAULT_MEMORY_MAX_TEXT_CHARS,
            ),
            max_records_per_turn=_parse_positive_int(
                ENV_MEMORY_MAX_RECORDS_PER_TURN,
                get_env(ENV_MEMORY_MAX_RECORDS_PER_TURN),
                DEFAULT_MEMORY_MAX_RECORDS_PER_TURN,
            ),
        )


@dataclass(frozen=True, slots=True)
class RagSettings:
    """How the RAG pipeline chunks documents and how many chunks it retrieves.

    The values come from ADR-0009, which is where the 400/800/1200 chunk-size
    experiment of ``docs/records/phase-5-rag.md`` is written down. They are
    settings and not constants so Phase 8 can replay that experiment by setting
    ``MYAGENT_RAG_CHUNK_SIZE`` instead of editing the chunker.

    ``enabled`` is the Phase 6 switch and it is narrower than memory's: it turns
    retrieval into the *agent's context* on and off
    (:meth:`myagent.rag.pipeline.RagPipeline.recall`), while ``myagent ingest`` /
    ``search`` / ``docs`` keep working — they are explicit commands, not "what the
    agent gets to see". That is the knob the Phase 8 ON/OFF comparison flips.
    """

    enabled: bool = DEFAULT_RAG_ENABLED
    chunk_size: int = DEFAULT_RAG_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_RAG_CHUNK_OVERLAP
    top_k: int = DEFAULT_RAG_TOP_K

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError(f"{ENV_RAG_CHUNK_SIZE} must be positive, got {self.chunk_size}")
        if self.chunk_overlap < 0:
            raise ValueError(
                f"{ENV_RAG_CHUNK_OVERLAP} must not be negative, got {self.chunk_overlap}"
            )
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"{ENV_RAG_CHUNK_OVERLAP} ({self.chunk_overlap}) must be smaller than "
                f"{ENV_RAG_CHUNK_SIZE} ({self.chunk_size}); otherwise a window could "
                "never grow past its overlap"
            )
        if self.top_k <= 0:
            raise ValueError(f"{ENV_RAG_TOP_K} must be positive, got {self.top_k}")

    @classmethod
    def from_env(cls) -> RagSettings:
        """Build settings from the environment, loading ``.env`` first."""
        load_env()
        return cls(
            enabled=get_bool_env(ENV_RAG_ENABLED, DEFAULT_RAG_ENABLED),
            chunk_size=_parse_positive_int(
                ENV_RAG_CHUNK_SIZE, get_env(ENV_RAG_CHUNK_SIZE), DEFAULT_RAG_CHUNK_SIZE
            ),
            chunk_overlap=_parse_non_negative_int(
                ENV_RAG_CHUNK_OVERLAP,
                get_env(ENV_RAG_CHUNK_OVERLAP),
                DEFAULT_RAG_CHUNK_OVERLAP,
            ),
            top_k=_parse_positive_int(ENV_RAG_TOP_K, get_env(ENV_RAG_TOP_K), DEFAULT_RAG_TOP_K),
        )


def _parse_dim(raw: str | None) -> int | None:
    """Parse ``EMBED_DIM``; blank means "let the service decide"."""
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"EMBED_DIM must be an integer, got {raw!r}") from exc


def _parse_positive_int(name: str, raw: str | None, default: int) -> int:
    """Parse a positive integer setting, keeping the default when unset."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _parse_non_negative_int(name: str, raw: str | None, default: int) -> int:
    """Parse an integer setting that may be zero, keeping the default when unset."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def _parse_positive_float(name: str, raw: str | None, default: float) -> float:
    """Parse a strictly positive float setting, keeping the default when unset."""
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _parse_temperature(raw: str | None) -> float:
    """Parse ``LLM_TEMPERATURE``; ``0`` is valid (it disables sampling jitter)."""
    if raw is None:
        return DEFAULT_LLM_TEMPERATURE
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_LLM_TEMPERATURE} must be a number, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{ENV_LLM_TEMPERATURE} must not be negative, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class LLMSettings:
    """How to reach the chat model (Phase 2: one OpenAI-compatible client).

    ``from_env()`` validates the *shape* of the configuration but does not require
    the model name or an API key, so offline commands (``myagent tools``) keep
    working. Call :meth:`require_model` / :meth:`require_api_key` right before the
    first request, which is where a missing value must fail loudly.
    """

    provider: str = DEFAULT_LLM_PROVIDER
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int = DEFAULT_LLM_MAX_TOKENS
    context_window: int = DEFAULT_LLM_CONTEXT_WINDOW
    temperature: float = DEFAULT_LLM_TEMPERATURE

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_LLM_PROVIDERS:
            supported = ", ".join(SUPPORTED_LLM_PROVIDERS)
            raise ValueError(
                f"unsupported LLM provider {self.provider!r}; expected one of {supported}"
            )
        if self.model is not None and not self.model.strip():
            raise ValueError("LLM_MODEL must not be blank (leave it unset instead)")
        if self.base_url is not None and not self.base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"LLM_BASE_URL must start with http:// or https://, got {self.base_url!r}"
            )
        if self.max_tokens <= 0:
            raise ValueError(f"LLM_MAX_TOKENS must be positive, got {self.max_tokens}")
        if self.context_window <= 0:
            raise ValueError(f"LLM_CONTEXT_WINDOW must be positive, got {self.context_window}")
        if self.temperature < 0:
            raise ValueError(f"LLM_TEMPERATURE must not be negative, got {self.temperature}")

    @classmethod
    def from_env(cls) -> LLMSettings:
        """Build settings from the environment, loading ``.env`` first.

        ``LLM_API_KEY`` wins; when it is blank, ``OPENAI_API_KEY`` is used so an
        existing shell export keeps working.
        """
        load_env()
        provider = (get_env(ENV_LLM_PROVIDER, DEFAULT_LLM_PROVIDER) or DEFAULT_LLM_PROVIDER).lower()
        fallback_key_var = _LLM_FALLBACK_API_KEY_VARS.get(provider)
        return cls(
            provider=provider,
            model=get_env(ENV_LLM_MODEL),
            api_key=get_env(ENV_LLM_API_KEY)
            or (get_env(fallback_key_var) if fallback_key_var else None),
            base_url=get_env(ENV_LLM_BASE_URL),
            max_tokens=_parse_positive_int(
                ENV_LLM_MAX_TOKENS, get_env(ENV_LLM_MAX_TOKENS), DEFAULT_LLM_MAX_TOKENS
            ),
            context_window=_parse_positive_int(
                ENV_LLM_CONTEXT_WINDOW,
                get_env(ENV_LLM_CONTEXT_WINDOW),
                DEFAULT_LLM_CONTEXT_WINDOW,
            ),
            temperature=_parse_temperature(get_env(ENV_LLM_TEMPERATURE)),
        )

    def require_model(self) -> str:
        """Return the model name, raising :class:`MissingEnvError` if unset."""
        if self.model:
            return self.model
        raise MissingEnvError(ENV_LLM_MODEL)

    def require_api_key(self) -> str:
        """Return the API key, raising :class:`MissingEnvError` if unset."""
        if self.api_key:
            return self.api_key
        raise MissingEnvError(ENV_LLM_API_KEY)

    def resolved_base_url(self) -> str:
        """Return the configured endpoint, or the provider's public default."""
        return self.base_url or _DEFAULT_LLM_BASE_URLS[self.provider]


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Runtime limits and filesystem roots for the agent loop."""

    max_iterations: int = DEFAULT_AGENT_MAX_ITERATIONS
    tool_timeout_s: float = DEFAULT_AGENT_TOOL_TIMEOUT_S
    max_tool_result_chars: int = DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS
    workspace: Path = DEFAULT_AGENT_WORKSPACE
    sessions_dir: Path = DEFAULT_AGENT_SESSIONS_DIR

    def __post_init__(self) -> None:
        if self.max_iterations <= 0:
            raise ValueError(f"AGENT_MAX_ITERATIONS must be positive, got {self.max_iterations}")
        if self.tool_timeout_s <= 0:
            raise ValueError(f"AGENT_TOOL_TIMEOUT_S must be positive, got {self.tool_timeout_s}")
        if self.max_tool_result_chars <= 0:
            raise ValueError(
                f"AGENT_MAX_TOOL_RESULT_CHARS must be positive, got {self.max_tool_result_chars}"
            )

    @classmethod
    def from_env(cls) -> AgentSettings:
        """Build settings from the environment, loading ``.env`` first."""
        load_env()
        workspace = get_env(ENV_AGENT_WORKSPACE)
        sessions_dir = get_env(ENV_AGENT_SESSIONS_DIR)
        return cls(
            max_iterations=_parse_positive_int(
                ENV_AGENT_MAX_ITERATIONS,
                get_env(ENV_AGENT_MAX_ITERATIONS),
                DEFAULT_AGENT_MAX_ITERATIONS,
            ),
            tool_timeout_s=_parse_positive_float(
                ENV_AGENT_TOOL_TIMEOUT_S,
                get_env(ENV_AGENT_TOOL_TIMEOUT_S),
                DEFAULT_AGENT_TOOL_TIMEOUT_S,
            ),
            max_tool_result_chars=_parse_positive_int(
                ENV_AGENT_MAX_TOOL_RESULT_CHARS,
                get_env(ENV_AGENT_MAX_TOOL_RESULT_CHARS),
                DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS,
            ),
            workspace=Path(workspace).expanduser() if workspace else DEFAULT_AGENT_WORKSPACE,
            sessions_dir=Path(sessions_dir).expanduser()
            if sessions_dir
            else DEFAULT_AGENT_SESSIONS_DIR,
        )


@dataclass(frozen=True, slots=True)
class Settings:
    """Every settings object the framework needs, read from one ``.env``.

    This is the single entry point for assembly (PLAN 3.5): ``build_agent()``
    takes one :class:`Settings` and passes each part to the component that needs
    it. Components never read environment variables themselves, so a test can
    hand them settings built by hand.
    """

    llm: LLMSettings
    agent: AgentSettings
    sqlite: SQLiteSettings
    qdrant: QdrantSettings
    embedding: EmbeddingSettings
    memory: MemorySettings = field(default_factory=MemorySettings)
    rag: RagSettings = field(default_factory=RagSettings)

    @classmethod
    def from_env(cls) -> Settings:
        """Build the whole settings bundle from the environment."""
        return cls(
            llm=LLMSettings.from_env(),
            agent=AgentSettings.from_env(),
            sqlite=SQLiteSettings.from_env(),
            qdrant=QdrantSettings.from_env(),
            embedding=EmbeddingSettings.from_env(),
            memory=MemorySettings.from_env(),
            rag=RagSettings.from_env(),
        )
