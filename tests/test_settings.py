"""Typed settings derived from the environment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from myagent.config.env import MissingEnvError
from myagent.config.settings import (
    DEFAULT_EMBED_MODEL_NAME,
    DEFAULT_EMBED_MODEL_TYPE,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_URL,
    DEFAULT_SQLITE_PATH,
    EmbeddingSettings,
    QdrantSettings,
    SQLiteSettings,
)


@pytest.fixture
def isolated_environ(monkeypatch):
    """Give each test a private copy of ``os.environ`` that is restored afterwards."""
    copy = dict(os.environ)
    monkeypatch.setattr(os, "environ", copy)
    return copy


@pytest.fixture(autouse=True)
def empty_env_file(tmp_path, isolated_environ, monkeypatch):
    """Keep the real project .env out of these tests."""
    monkeypatch.setenv("MYAGENT_ENV_FILE", str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("", encoding="utf-8")


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
    assert qdrant.prefer_grpc is True


def test_sqlite_path_expands_the_user_directory(isolated_environ):
    isolated_environ["MYAGENT_SQLITE_PATH"] = "~/myagent/docs.db"

    assert SQLiteSettings.from_env().path == Path("~/myagent/docs.db").expanduser()


def test_blank_qdrant_values_fall_back_to_defaults(isolated_environ):
    isolated_environ["MYAGENT_QDRANT_URL"] = ""
    isolated_environ["MYAGENT_QDRANT_COLLECTION"] = ""
    isolated_environ["MYAGENT_QDRANT_API_KEY"] = ""

    qdrant = QdrantSettings.from_env()

    assert qdrant.url == DEFAULT_QDRANT_URL
    assert qdrant.collection == DEFAULT_QDRANT_COLLECTION
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
