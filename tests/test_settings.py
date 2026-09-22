"""Typed settings derived from the environment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from myagent.config.env import MissingEnvError
from myagent.config.settings import (
    DEFAULT_AGENT_MAX_ITERATIONS,
    DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS,
    DEFAULT_AGENT_SESSIONS_DIR,
    DEFAULT_AGENT_TOOL_TIMEOUT_S,
    DEFAULT_AGENT_WORKSPACE,
    DEFAULT_EMBED_BATCH_SIZE,
    DEFAULT_EMBED_MODEL_NAME,
    DEFAULT_EMBED_MODEL_TYPE,
    DEFAULT_LLM_CONTEXT_WINDOW,
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_MEMORY_ENABLED,
    DEFAULT_MEMORY_HALF_LIFE_DAYS,
    DEFAULT_MEMORY_MAX_RECORDS_PER_TURN,
    DEFAULT_MEMORY_MAX_TEXT_CHARS,
    DEFAULT_MEMORY_TOP_K,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_MEMORY_COLLECTION,
    DEFAULT_QDRANT_URL,
    DEFAULT_RAG_CHUNK_OVERLAP,
    DEFAULT_RAG_CHUNK_SIZE,
    DEFAULT_RAG_TOP_K,
    DEFAULT_SQLITE_PATH,
    AgentSettings,
    EmbeddingSettings,
    LLMSettings,
    MemorySettings,
    QdrantSettings,
    RagSettings,
    Settings,
    SQLiteSettings,
)


@pytest.fixture
def isolated_environ(monkeypatch):
    """Give each test a private copy of ``os.environ`` that is restored afterwards."""
    copy = dict(os.environ)
    monkeypatch.setattr(os, "environ", copy)
    return copy


def test_storage_defaults_need_no_configuration():
    assert SQLiteSettings.from_env().path == DEFAULT_SQLITE_PATH

    qdrant = QdrantSettings.from_env()
    assert qdrant.url == DEFAULT_QDRANT_URL
    assert qdrant.collection == DEFAULT_QDRANT_COLLECTION
    assert qdrant.api_key is None
    assert qdrant.prefer_grpc is False


def test_embedding_defaults_are_dashscope():
    embedding = EmbeddingSettings.from_env()

    assert embedding.model_type == DEFAULT_EMBED_MODEL_TYPE == "dashscope"
    assert embedding.model_name == DEFAULT_EMBED_MODEL_NAME == "qwen3.7-text-embedding-flash"
    assert embedding.api_key is None
    assert embedding.resolved_base_url() == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_embedding_reads_the_env_file(isolated_environ, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "EMBED_MODEL_TYPE=openai",
                "EMBED_MODEL_NAME=text-embedding-3-small",
                "EMBED_API_KEY=sk-embed",
                "EMBED_BASE_URL=https://embeddings.example.com/v1",
                "EMBED_DIM=1536",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    isolated_environ["MYAGENT_ENV_FILE"] = str(env_file)

    embedding = EmbeddingSettings.from_env()

    assert embedding.model_type == "openai"
    assert embedding.model_name == "text-embedding-3-small"
    assert embedding.api_key == "sk-embed"
    assert embedding.resolved_base_url() == "https://embeddings.example.com/v1"
    assert embedding.dim == 1536
    assert embedding.require_api_key() == "sk-embed"


def test_embedding_falls_back_to_the_provider_api_key_variable(isolated_environ):
    isolated_environ["EMBED_API_KEY"] = ""
    isolated_environ["DASHSCOPE_API_KEY"] = "dashscope-key"

    assert EmbeddingSettings.from_env().api_key == "dashscope-key"


def test_embedding_falls_back_to_the_openai_api_key_variable(isolated_environ):
    isolated_environ["EMBED_MODEL_TYPE"] = "OPENAI"
    isolated_environ["EMBED_API_KEY"] = ""
    isolated_environ["OPENAI_API_KEY"] = "openai-key"

    embedding = EmbeddingSettings.from_env()

    assert embedding.model_type == "openai"
    assert embedding.api_key == "openai-key"
    assert embedding.resolved_base_url() == "https://api.openai.com/v1"


def test_embedding_requires_an_api_key_before_use():
    with pytest.raises(MissingEnvError, match="EMBED_API_KEY"):
        EmbeddingSettings().require_api_key()


def test_embedding_base_url_can_be_blank(isolated_environ):
    isolated_environ["EMBED_BASE_URL"] = ""

    assert EmbeddingSettings.from_env().resolved_base_url() is not None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model_type": "cohere"}, "unsupported embedding model type"),
        ({"model_name": "   "}, "EMBED_MODEL_NAME must not be empty"),
        ({"base_url": "dashscope.aliyuncs.com"}, "EMBED_BASE_URL must start with"),
        ({"dim": 0}, "EMBED_DIM must be a positive integer"),
    ],
)
def test_embedding_settings_are_validated(kwargs, message):
    with pytest.raises(ValueError, match=message):
        EmbeddingSettings(**kwargs)


def test_embedding_dim_must_be_an_integer(isolated_environ):
    isolated_environ["EMBED_DIM"] = "many"

    with pytest.raises(ValueError, match="EMBED_DIM must be an integer"):
        EmbeddingSettings.from_env()


def test_settings_read_the_env_file(isolated_environ, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"MYAGENT_SQLITE_PATH={tmp_path / 'docs.db'}",
                "MYAGENT_QDRANT_URL=https://qdrant.example.com:6333",
                "MYAGENT_QDRANT_API_KEY=qdrant-secret",
                "MYAGENT_QDRANT_COLLECTION=papers",
                "MYAGENT_QDRANT_MEMORY_COLLECTION=papers_memories",
                "MYAGENT_QDRANT_PREFER_GRPC=yes",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    isolated_environ["MYAGENT_ENV_FILE"] = str(env_file)

    sqlite = SQLiteSettings.from_env()
    qdrant = QdrantSettings.from_env()

    assert sqlite.path == tmp_path / "docs.db"
    assert qdrant.url == "https://qdrant.example.com:6333"
    assert qdrant.api_key == "qdrant-secret"
    assert qdrant.collection == "papers"
    assert qdrant.memory_collection == "papers_memories"
    assert qdrant.prefer_grpc is True


def test_sqlite_path_expands_the_user_directory(isolated_environ):
    isolated_environ["MYAGENT_SQLITE_PATH"] = "~/myagent/docs.db"

    assert SQLiteSettings.from_env().path == Path("~/myagent/docs.db").expanduser()


def test_blank_qdrant_values_fall_back_to_defaults(isolated_environ):
    isolated_environ["MYAGENT_QDRANT_URL"] = ""
    isolated_environ["MYAGENT_QDRANT_COLLECTION"] = ""
    isolated_environ["MYAGENT_QDRANT_MEMORY_COLLECTION"] = ""
    isolated_environ["MYAGENT_QDRANT_API_KEY"] = ""

    qdrant = QdrantSettings.from_env()

    assert qdrant.url == DEFAULT_QDRANT_URL
    assert qdrant.collection == DEFAULT_QDRANT_COLLECTION
    assert qdrant.memory_collection == DEFAULT_QDRANT_MEMORY_COLLECTION
    assert qdrant.api_key is None


def test_client_kwargs_omit_an_empty_api_key():
    local = QdrantSettings()
    remote = QdrantSettings(url="https://qdrant.example.com", api_key="secret")

    assert local.client_kwargs() == {"url": DEFAULT_QDRANT_URL, "prefer_grpc": False}
    assert remote.client_kwargs()["api_key"] == "secret"


@pytest.mark.parametrize("url", ["localhost:6333", "qdrant.example.com", ""])
def test_qdrant_url_must_be_http(url):
    with pytest.raises(ValueError, match="Qdrant url must start with"):
        QdrantSettings(url=url)


@pytest.mark.parametrize("collection", ["", " with space", "-leading-dash"])
def test_qdrant_collection_name_is_validated(collection):
    with pytest.raises(ValueError, match="invalid Qdrant collection name"):
        QdrantSettings(collection=collection)


@pytest.mark.parametrize("collection", ["", " with space"])
def test_qdrant_memory_collection_name_is_validated(collection):
    with pytest.raises(ValueError, match="invalid Qdrant memory_collection name"):
        QdrantSettings(memory_collection=collection)


def test_the_two_qdrant_collections_must_differ():
    """ADR-0008: memories and documents must not share an index."""
    with pytest.raises(ValueError, match="must differ"):
        QdrantSettings(collection="same", memory_collection="same")


def test_llm_defaults_need_no_configuration():
    llm = LLMSettings.from_env()

    assert llm.provider == DEFAULT_LLM_PROVIDER == "openai_compat"
    assert llm.model is None
    assert llm.api_key is None
    assert llm.base_url is None
    assert llm.resolved_base_url() == "https://api.openai.com/v1"
    assert llm.max_tokens == DEFAULT_LLM_MAX_TOKENS
    assert llm.context_window == DEFAULT_LLM_CONTEXT_WINDOW
    assert llm.temperature == DEFAULT_LLM_TEMPERATURE


def test_llm_reads_the_env_file(isolated_environ, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=OpenAI_Compat",
                "LLM_MODEL=deepseek-v4.1-flash",
                "LLM_API_KEY=sk-llm",
                "LLM_BASE_URL=https://example.com/compatible-mode/v1",
                "LLM_MAX_TOKENS=2048",
                "LLM_CONTEXT_WINDOW=64000",
                "LLM_TEMPERATURE=0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    isolated_environ["MYAGENT_ENV_FILE"] = str(env_file)

    llm = LLMSettings.from_env()

    assert llm.provider == "openai_compat"
    assert llm.require_model() == "deepseek-v4.1-flash"
    assert llm.require_api_key() == "sk-llm"
    assert llm.resolved_base_url() == "https://example.com/compatible-mode/v1"
    assert llm.max_tokens == 2048
    assert llm.context_window == 64000
    assert llm.temperature == 0


def test_llm_api_key_falls_back_to_the_openai_variable(isolated_environ):
    isolated_environ["LLM_API_KEY"] = ""
    isolated_environ["OPENAI_API_KEY"] = "openai-key"

    assert LLMSettings.from_env().api_key == "openai-key"


def test_llm_requires_a_model_and_a_key_before_use():
    with pytest.raises(MissingEnvError, match="LLM_MODEL"):
        LLMSettings().require_model()
    with pytest.raises(MissingEnvError, match="LLM_API_KEY"):
        LLMSettings().require_api_key()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provider": "anthropic"}, "unsupported LLM provider"),
        ({"model": "   "}, "LLM_MODEL must not be blank"),
        ({"base_url": "example.com/v1"}, "LLM_BASE_URL must start with"),
        ({"max_tokens": 0}, "LLM_MAX_TOKENS must be positive"),
        ({"context_window": -1}, "LLM_CONTEXT_WINDOW must be positive"),
        ({"temperature": -0.1}, "LLM_TEMPERATURE must not be negative"),
    ],
)
def test_llm_settings_are_validated(kwargs, message):
    with pytest.raises(ValueError, match=message):
        LLMSettings(**kwargs)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("LLM_MAX_TOKENS", "many", "LLM_MAX_TOKENS must be an integer"),
        ("LLM_MAX_TOKENS", "0", "LLM_MAX_TOKENS must be positive"),
        ("LLM_CONTEXT_WINDOW", "0", "LLM_CONTEXT_WINDOW must be positive"),
        ("LLM_TEMPERATURE", "warm", "LLM_TEMPERATURE must be a number"),
        ("LLM_TEMPERATURE", "-1", "LLM_TEMPERATURE must not be negative"),
    ],
)
def test_llm_numbers_are_parsed_from_the_environment(isolated_environ, name, value, message):
    isolated_environ[name] = value

    with pytest.raises(ValueError, match=message):
        LLMSettings.from_env()


def test_agent_defaults_need_no_configuration():
    agent = AgentSettings.from_env()

    assert agent.max_iterations == DEFAULT_AGENT_MAX_ITERATIONS
    assert agent.tool_timeout_s == DEFAULT_AGENT_TOOL_TIMEOUT_S
    assert agent.max_tool_result_chars == DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS
    assert agent.workspace == DEFAULT_AGENT_WORKSPACE
    assert agent.sessions_dir == DEFAULT_AGENT_SESSIONS_DIR


def test_agent_settings_read_the_env_file(isolated_environ, tmp_path):
    isolated_environ["AGENT_MAX_ITERATIONS"] = "3"
    isolated_environ["AGENT_TOOL_TIMEOUT_S"] = "1.5"
    isolated_environ["AGENT_MAX_TOOL_RESULT_CHARS"] = "128"
    isolated_environ["AGENT_WORKSPACE"] = "~/work"
    isolated_environ["AGENT_SESSIONS_DIR"] = "~/sessions"

    agent = AgentSettings.from_env()

    assert agent.max_iterations == 3
    assert agent.tool_timeout_s == 1.5
    assert agent.max_tool_result_chars == 128
    assert agent.workspace == Path("~/work").expanduser()
    assert agent.sessions_dir == Path("~/sessions").expanduser()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("AGENT_MAX_ITERATIONS", "lots", "AGENT_MAX_ITERATIONS must be an integer"),
        ("AGENT_MAX_ITERATIONS", "0", "AGENT_MAX_ITERATIONS must be positive"),
        ("AGENT_TOOL_TIMEOUT_S", "0", "AGENT_TOOL_TIMEOUT_S must be positive"),
        ("AGENT_TOOL_TIMEOUT_S", "soon", "AGENT_TOOL_TIMEOUT_S must be a number"),
        (
            "AGENT_MAX_TOOL_RESULT_CHARS",
            "-5",
            "AGENT_MAX_TOOL_RESULT_CHARS must be positive",
        ),
    ],
)
def test_agent_numbers_are_parsed_from_the_environment(isolated_environ, name, value, message):
    isolated_environ[name] = value

    with pytest.raises(ValueError, match=message):
        AgentSettings.from_env()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_iterations": 0}, "AGENT_MAX_ITERATIONS must be positive"),
        ({"tool_timeout_s": 0}, "AGENT_TOOL_TIMEOUT_S must be positive"),
        ({"max_tool_result_chars": 0}, "AGENT_MAX_TOOL_RESULT_CHARS must be positive"),
    ],
)
def test_agent_settings_are_validated(kwargs, message):
    with pytest.raises(ValueError, match=message):
        AgentSettings(**kwargs)


def test_settings_from_env_bundles_every_section(isolated_environ):
    isolated_environ["LLM_MODEL"] = "test-model"
    isolated_environ["AGENT_WORKSPACE"] = "/tmp/ws"
    isolated_environ["MYAGENT_SQLITE_PATH"] = "/tmp/myagent.db"

    settings = Settings.from_env()

    assert isinstance(settings, Settings)
    assert settings.llm.model == "test-model"
    assert settings.agent.workspace == Path("/tmp/ws")
    assert settings.sqlite.path == Path("/tmp/myagent.db")
    assert isinstance(settings.qdrant, QdrantSettings)
    assert isinstance(settings.embedding, EmbeddingSettings)
    assert isinstance(settings.memory, MemorySettings)


# --------------------------------------------------------------------------
# Phase 4: the memory settings
# --------------------------------------------------------------------------


def test_memory_defaults_need_no_configuration():
    memory = MemorySettings.from_env()

    assert memory.enabled is DEFAULT_MEMORY_ENABLED
    assert memory.half_life_days == DEFAULT_MEMORY_HALF_LIFE_DAYS
    assert memory.top_k == DEFAULT_MEMORY_TOP_K
    assert memory.max_text_chars == DEFAULT_MEMORY_MAX_TEXT_CHARS
    assert memory.max_records_per_turn == DEFAULT_MEMORY_MAX_RECORDS_PER_TURN


def test_memory_defaults_agree_with_the_write_policy():
    """``MemorySettings.min_importance`` and the record default must not drift."""
    from myagent.memory.types import DEFAULT_IMPORTANCE

    assert MemorySettings().min_importance == DEFAULT_IMPORTANCE


def test_memory_reads_the_env_file(isolated_environ, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MYAGENT_MEMORY_ENABLED=off",
                "MYAGENT_MEMORY_HALF_LIFE_DAYS=7",
                "MYAGENT_MEMORY_TOP_K=3",
                "MYAGENT_MEMORY_MAX_TEXT_CHARS=120",
                "MYAGENT_MEMORY_MAX_RECORDS_PER_TURN=1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    isolated_environ["MYAGENT_ENV_FILE"] = str(env_file)

    memory = MemorySettings.from_env()

    assert memory.enabled is False
    assert memory.half_life_days == 7.0
    assert memory.top_k == 3
    assert memory.max_text_chars == 120
    assert memory.max_records_per_turn == 1


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MYAGENT_MEMORY_HALF_LIFE_DAYS", "0", "must be positive"),
        ("MYAGENT_MEMORY_HALF_LIFE_DAYS", "soon", "must be a number"),
        ("MYAGENT_MEMORY_TOP_K", "0", "must be positive"),
        ("MYAGENT_MEMORY_MAX_TEXT_CHARS", "many", "must be an integer"),
        ("MYAGENT_MEMORY_MAX_RECORDS_PER_TURN", "-1", "must be positive"),
        ("MYAGENT_MEMORY_ENABLED", "maybe", "must be a boolean"),
    ],
)
def test_memory_numbers_are_parsed_from_the_environment(isolated_environ, name, value, message):
    isolated_environ[name] = value

    with pytest.raises(ValueError, match=message):
        MemorySettings.from_env()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"half_life_days": 0}, "MYAGENT_MEMORY_HALF_LIFE_DAYS must be positive"),
        ({"top_k": 0}, "MYAGENT_MEMORY_TOP_K must be positive"),
        ({"max_text_chars": 0}, "MYAGENT_MEMORY_MAX_TEXT_CHARS must be positive"),
        ({"max_records_per_turn": 0}, "MYAGENT_MEMORY_MAX_RECORDS_PER_TURN must be positive"),
        ({"min_importance": 1.5}, "min_importance must be within 0..1"),
        ({"dedup_threshold": -0.1}, "dedup_threshold must be within 0..1"),
        ({"dedup_recent": -1}, "dedup_recent must not be negative"),
        ({"short_query_chars": -1}, "short_query_chars must not be negative"),
    ],
)
def test_memory_settings_are_validated(kwargs, message):
    with pytest.raises(ValueError, match=message):
        MemorySettings(**kwargs)


# --- Phase 5: RAG ------------------------------------------------------------


def test_rag_defaults_are_the_adr_0009_values():
    rag = RagSettings.from_env()

    assert rag == RagSettings()
    assert (
        (rag.chunk_size, rag.chunk_overlap, rag.top_k)
        == (
            DEFAULT_RAG_CHUNK_SIZE,
            DEFAULT_RAG_CHUNK_OVERLAP,
            DEFAULT_RAG_TOP_K,
        )
        == (800, 120, 5)
    )


def test_rag_reads_the_env_file(isolated_environ, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MYAGENT_RAG_CHUNK_SIZE=400",
                "MYAGENT_RAG_CHUNK_OVERLAP=0",
                "MYAGENT_RAG_TOP_K=10",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    isolated_environ["MYAGENT_ENV_FILE"] = str(env_file)

    rag = RagSettings.from_env()

    assert (rag.chunk_size, rag.chunk_overlap, rag.top_k) == (400, 0, 10)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"chunk_size": 0}, "MYAGENT_RAG_CHUNK_SIZE must be positive"),
        ({"chunk_overlap": -1}, "MYAGENT_RAG_CHUNK_OVERLAP must not be negative"),
        ({"chunk_size": 100, "chunk_overlap": 100}, "must be smaller than"),
        ({"top_k": 0}, "MYAGENT_RAG_TOP_K must be positive"),
    ],
)
def test_rag_settings_are_validated(kwargs, message):
    with pytest.raises(ValueError, match=message):
        RagSettings(**kwargs)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MYAGENT_RAG_CHUNK_SIZE", "many", "must be an integer"),
        ("MYAGENT_RAG_CHUNK_OVERLAP", "few", "must be an integer"),
        ("MYAGENT_RAG_CHUNK_OVERLAP", "-5", "must not be negative"),
        ("MYAGENT_RAG_TOP_K", "0", "must be positive"),
    ],
)
def test_rag_numbers_are_parsed_from_the_environment(isolated_environ, name, value, message):
    isolated_environ[name] = value

    with pytest.raises(ValueError, match=message):
        RagSettings.from_env()


def test_every_settings_bundle_carries_rag(isolated_environ):
    assert Settings.from_env().rag == RagSettings()


def test_the_embedding_batch_size_defaults_and_reads_the_environment(isolated_environ):
    assert EmbeddingSettings.from_env().batch_size == DEFAULT_EMBED_BATCH_SIZE == 16

    isolated_environ["EMBED_BATCH_SIZE"] = "4"

    assert EmbeddingSettings.from_env().batch_size == 4
    with pytest.raises(ValueError, match="EMBED_BATCH_SIZE must be positive"):
        EmbeddingSettings(batch_size=0)
    isolated_environ["EMBED_BATCH_SIZE"] = "-1"
    with pytest.raises(ValueError, match="EMBED_BATCH_SIZE must be positive"):
        EmbeddingSettings.from_env()
