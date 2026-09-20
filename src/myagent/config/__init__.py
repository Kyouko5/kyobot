"""Configuration and secret loading for MyAgent."""

from __future__ import annotations

from myagent.config.env import (
    ENV_FILE_VAR,
    MissingEnvError,
    discover_env_file,
    get_bool_env,
    get_env,
    load_env,
    require_env,
)
from myagent.config.settings import (
    DEFAULT_AGENT_MAX_ITERATIONS,
    DEFAULT_AGENT_SESSIONS_DIR,
    DEFAULT_AGENT_WORKSPACE,
    DEFAULT_EMBED_MODEL_NAME,
    DEFAULT_EMBED_MODEL_TYPE,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_URL,
    DEFAULT_SQLITE_PATH,
    SUPPORTED_EMBED_MODEL_TYPES,
    SUPPORTED_LLM_PROVIDERS,
    AgentSettings,
    EmbeddingSettings,
    LLMSettings,
    QdrantSettings,
    Settings,
    SQLiteSettings,
)

__all__ = [
    "DEFAULT_AGENT_MAX_ITERATIONS",
    "DEFAULT_AGENT_SESSIONS_DIR",
    "DEFAULT_AGENT_WORKSPACE",
    "DEFAULT_EMBED_MODEL_NAME",
    "DEFAULT_EMBED_MODEL_TYPE",
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_QDRANT_COLLECTION",
    "DEFAULT_QDRANT_URL",
    "DEFAULT_SQLITE_PATH",
    "ENV_FILE_VAR",
    "SUPPORTED_EMBED_MODEL_TYPES",
    "SUPPORTED_LLM_PROVIDERS",
    "AgentSettings",
    "EmbeddingSettings",
    "LLMSettings",
    "MissingEnvError",
    "QdrantSettings",
    "SQLiteSettings",
    "Settings",
    "discover_env_file",
    "get_bool_env",
    "get_env",
    "load_env",
    "require_env",
]
