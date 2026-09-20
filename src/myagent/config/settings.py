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

    @classmethod
    def from_env(cls) -> Settings:
        """Build the whole settings bundle from the environment."""
        return cls(
            llm=LLMSettings.from_env(),
            agent=AgentSettings.from_env(),
            sqlite=SQLiteSettings.from_env(),
            qdrant=QdrantSettings.from_env(),
            embedding=EmbeddingSettings.from_env(),
        )
