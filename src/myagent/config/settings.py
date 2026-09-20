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
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from myagent.config.env import MissingEnvError, get_bool_env, get_env, load_env

ENV_SQLITE_PATH: Final = "MYAGENT_SQLITE_PATH"
ENV_QDRANT_URL: Final = "MYAGENT_QDRANT_URL"
ENV_QDRANT_API_KEY: Final = "MYAGENT_QDRANT_API_KEY"
ENV_QDRANT_COLLECTION: Final = "MYAGENT_QDRANT_COLLECTION"
ENV_QDRANT_PREFER_GRPC: Final = "MYAGENT_QDRANT_PREFER_GRPC"

DEFAULT_SQLITE_PATH: Final = Path("data/myagent.db")
DEFAULT_QDRANT_URL: Final = "http://localhost:6333"
DEFAULT_QDRANT_COLLECTION: Final = "myagent_documents"

_COLLECTION_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

ENV_EMBED_MODEL_TYPE: Final = "EMBED_MODEL_TYPE"
ENV_EMBED_MODEL_NAME: Final = "EMBED_MODEL_NAME"
ENV_EMBED_API_KEY: Final = "EMBED_API_KEY"
ENV_EMBED_BASE_URL: Final = "EMBED_BASE_URL"
ENV_EMBED_DIM: Final = "EMBED_DIM"

SUPPORTED_EMBED_MODEL_TYPES: Final = ("dashscope", "openai")
DEFAULT_EMBED_MODEL_TYPE: Final = "dashscope"
DEFAULT_EMBED_MODEL_NAME: Final = "qwen3.7-text-embedding-flash"

_FALLBACK_API_KEY_VARS: Final = {
    "dashscope": "DASHSCOPE_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_DEFAULT_EMBED_BASE_URLS: Final = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}


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
    """How to reach the Qdrant instance holding the vector indexes."""

    url: str = DEFAULT_QDRANT_URL
    collection: str = DEFAULT_QDRANT_COLLECTION
    api_key: str | None = None
    prefer_grpc: bool = False

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"Qdrant url must start with http:// or https://, got {self.url!r}")
        if not _COLLECTION_PATTERN.match(self.collection):
            raise ValueError(f"invalid Qdrant collection name: {self.collection!r}")

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
        )

    def resolved_base_url(self) -> str | None:
        """Explicit ``EMBED_BASE_URL``, otherwise the provider's OpenAI-compatible endpoint."""
        return self.base_url or _DEFAULT_EMBED_BASE_URLS.get(self.model_type)

    def require_api_key(self) -> str:
        """Return the embedding API key, raising :class:`MissingEnvError` if unset."""
        if self.api_key:
            return self.api_key
        raise MissingEnvError(ENV_EMBED_API_KEY)


def _parse_dim(raw: str | None) -> int | None:
    """Parse ``EMBED_DIM``; blank means "let the service decide"."""
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"EMBED_DIM must be an integer, got {raw!r}") from exc
