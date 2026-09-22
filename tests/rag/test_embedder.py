"""The embedders of PLAN 5.4: provider selection, batching, retries, normalization."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from myagent.config.settings import EmbeddingSettings
from myagent.rag import embedder as embedder_module
from myagent.rag.embedder import (
    BaseEmbedder,
    DashScopeEmbedder,
    EmbeddingError,
    OpenAICompatEmbedder,
    OpenAIEmbedder,
    build_embedder,
    normalize_vector,
)

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
OPENAI_URL = "https://api.openai.com/v1"


class FakeEmbeddings:
    """``client.embeddings`` that records batches and can fail on demand."""

    def __init__(self, vector: list[float], *, fail_times: int = 0) -> None:
        self.vector = vector
        self.fail_times = fail_times
        self.batches: list[list[str]] = []

    async def create(self, *, model: str, input: list[str]):
        self.batches.append(list(input))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("503 from the provider")
        data = [
            SimpleNamespace(index=index, embedding=list(self.vector))
            for index, _ in enumerate(input)
        ]
        return SimpleNamespace(data=data)


class FakeClient:
    """The bit of ``AsyncOpenAI`` the embedder uses."""

    def __init__(self, vector: list[float] | None = None, *, fail_times: int = 0) -> None:
        self.embeddings = FakeEmbeddings(vector or [3.0, 4.0], fail_times=fail_times)


class MuteClient:
    """A client that answers 200 with an empty ``data`` list (a broken provider)."""

    def __init__(self) -> None:
        self.embeddings = self

    async def create(self, *, model: str, input: list[str]):
        return SimpleNamespace(data=[])


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    """No sleeping in the retry tests."""
    monkeypatch.setattr(embedder_module, "_BACKOFF_S", 0.0)


# --- the provider classes ----------------------------------------------------


def test_the_provider_classes_are_base_embedders_that_pin_their_endpoint():
    dashscope = DashScopeEmbedder()
    openai = OpenAIEmbedder()

    assert isinstance(dashscope, BaseEmbedder)
    assert isinstance(openai, BaseEmbedder)
    assert isinstance(dashscope, OpenAICompatEmbedder)
    assert dashscope.settings.model_type == "dashscope"
    assert dashscope.settings.resolved_base_url() == DASHSCOPE_URL
    assert openai.settings.model_type == "openai"
    assert openai.settings.resolved_base_url() == OPENAI_URL


def test_the_class_name_wins_over_a_contradicting_model_type():
    """``DashScopeEmbedder`` cannot silently end up on OpenAI's endpoint."""
    settings = EmbeddingSettings(model_type="openai", model_name="m", api_key="k")

    dashscope = DashScopeEmbedder(settings)

    assert dashscope.settings.model_type == "dashscope"
    assert dashscope.settings.model_name == "m"  # everything else is untouched
    assert dashscope.settings.resolved_base_url() == DASHSCOPE_URL


def test_build_embedder_dispatches_on_the_model_type():
    dashscope = build_embedder(EmbeddingSettings(model_type="dashscope"))
    openai = build_embedder(EmbeddingSettings(model_type="openai"))

    assert isinstance(dashscope, DashScopeEmbedder)
    assert isinstance(openai, OpenAIEmbedder)
    assert (dashscope.settings.model_type, openai.settings.model_type) == ("dashscope", "openai")


def test_the_default_settings_are_the_dashscope_ones():
    dashscope = DashScopeEmbedder()

    assert dashscope.settings.model_name == "qwen3.7-text-embedding-flash"
    assert dashscope.settings.dim is None
    assert dashscope.settings.batch_size == 16
    assert dashscope.normalized is True


# --- normalization -----------------------------------------------------------


def test_normalize_vector_makes_a_unit_vector():
    assert normalize_vector([3.0, 4.0]) == [0.6, 0.8]
    assert normalize_vector([0.0, 0.0]) == [0.0, 0.0]  # no direction, no crash
    assert normalize_vector([]) == []
    assert normalize_vector([-3.0, -4.0]) == [-0.6, -0.8]


async def test_the_provider_embedders_normalize_by_default():
    embedder = DashScopeEmbedder(
        EmbeddingSettings(model_name="m", api_key="k"), client=FakeClient()
    )

    vectors = await embedder.embed(["a"])

    assert vectors == [[0.6, 0.8]]
    assert embedder.dim == 2


async def test_normalization_can_be_disabled():
    embedder = OpenAIEmbedder(
        EmbeddingSettings(model_name="m", api_key="k"), client=FakeClient(), normalize=False
    )

    assert embedder.normalized is False
    assert await embedder.embed(["a"]) == [[3.0, 4.0]]


# --- batching, retries, errors ----------------------------------------------


async def test_the_batch_size_comes_from_the_settings():
    client = FakeClient()
    embedder = OpenAICompatEmbedder(
        EmbeddingSettings(model_name="m", api_key="k", batch_size=4), client=client
    )

    vectors = await embedder.embed([f"text-{index}" for index in range(10)])

    assert len(vectors) == 10
    assert [len(batch) for batch in client.embeddings.batches] == [4, 4, 2]


async def test_embedding_nothing_asks_the_provider_nothing():
    client = FakeClient()
    embedder = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", api_key="k"), client=client)

    assert await embedder.embed([]) == []
    assert client.embeddings.batches == []


async def test_a_transient_failure_is_retried():
    client = FakeClient(fail_times=2)
    embedder = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", api_key="k"), client=client)

    assert len(await embedder.embed(["a"])) == 1
    assert len(client.embeddings.batches) == 3


async def test_a_permanent_failure_becomes_one_readable_error():
    client = FakeClient(fail_times=99)
    embedder = OpenAICompatEmbedder(
        EmbeddingSettings(model_name="small-model", api_key="k"), client=client
    )

    with pytest.raises(EmbeddingError, match="small-model"):
        await embedder.embed(["a"])


async def test_dim_comes_from_the_settings_then_from_the_first_observation():
    configured = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", dim=8))
    observed = OpenAICompatEmbedder(EmbeddingSettings(model_name="m"), client=FakeClient())

    assert configured.dim == 8
    with pytest.raises(EmbeddingError, match="EMBED_DIM is unset"):
        _ = observed.dim

    await observed.embed(["a"])

    assert observed.dim == 2


async def test_a_provider_that_answers_nothing_leaves_the_dimension_unknown():
    embedder = OpenAICompatEmbedder(EmbeddingSettings(model_name="m"), client=MuteClient())

    assert await embedder.embed(["a"]) == []
    with pytest.raises(EmbeddingError, match="EMBED_DIM is unset"):
        _ = embedder.dim


def test_the_client_is_built_lazily_from_the_settings(monkeypatch):
    built: list[dict[str, object]] = []

    class Recorder:
        def __init__(self, **kwargs: object) -> None:
            built.append(kwargs)

    monkeypatch.setattr(embedder_module, "AsyncOpenAI", Recorder)
    embedder = DashScopeEmbedder(EmbeddingSettings(model_name="m", api_key="k"))

    assert embedder.client is embedder.client

    assert built == [{"api_key": "k", "base_url": DASHSCOPE_URL}]
    assert embedder.settings.model_name == "m"
