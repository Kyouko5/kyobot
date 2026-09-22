"""The embedding contract and the embedders that satisfy it (PLAN 5.4).

Two things live in this module and they are deliberately kept apart:

* :class:`BaseEmbedder` — the Phase 3 contract (``embed(texts)`` / ``dim``), the
  only thing the rest of the framework type-checks against;
* :class:`OpenAICompatEmbedder` — the transport: batching, three attempts with
  exponential backoff, one readable error type, an injectable client;
  :class:`DashScopeEmbedder` / :class:`OpenAIEmbedder` — the two providers of
  ADR-0005 / PLAN 5.4, which pin the endpoint and normalize by default.

The transport started in ``myagent.memory.embedder`` (Phase 4 needed one working
embedder before Phase 5 existed). Phase 5 moved it here because RAG owns
embedding: memory already imports this package for the ``BaseEmbedder`` contract,
so the dependency direction (``memory`` → ``rag``) has to point this way. Nothing
about the behaviour changed — Phase 4's memory tests were only pointed at the new
module path.

Operational details:

* **Batching** (``EMBED_BATCH_SIZE``, 16 by default): a turn's memories are few,
  but a document ingest is not, and the endpoint rejects oversized batches.
* **Three attempts with exponential backoff**: an embedding call is on the
  critical path of a write, and providers do return transient 5xx/timeouts.
  When the attempts run out the caller gets :class:`EmbeddingError` — the memory
  retriever degrades to keyword search (PLAN 4.4) instead of failing the turn.
* **Normalization** (PLAN 5.4): ``DashScopeEmbedder`` / ``OpenAIEmbedder`` scale
  every vector to unit length, so cosine similarity is a dot product and the
  stored vectors cannot drift in magnitude. The transport defaults it *off* so
  Phase 4's memory behaviour and its vector fixtures stay exactly as they were.
* **The dimension is either configured or observed** (``dim``): ``EMBED_DIM``
  wins, otherwise the length of the first vector the provider returns is
  remembered for the rest of the process. ``RagPipeline.ingest`` writes that
  observation back to ``.env`` so the Qdrant collection and every later run agree
  on it (``src/myagent/rag/pipeline.py``).
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import replace
from typing import Final, Protocol, runtime_checkable

from openai import AsyncOpenAI
from openai.types import CreateEmbeddingResponse

from myagent.config.settings import EmbeddingSettings
from myagent.observability.logging import get_logger

__all__ = [
    "DashScopeEmbedder",
    "EmbeddingError",
    "OpenAICompatEmbedder",
    "OpenAIEmbedder",
    "build_embedder",
    "normalize_vector",
]

logger = get_logger(__name__)

# The two values ``EMBED_MODEL_TYPE`` accepts (``SUPPORTED_EMBED_MODEL_TYPES`` in
# myagent/config/settings.py); spelled again here because these are the class
# names of this module.
DASHSCOPE: Final = "dashscope"
OPENAI: Final = "openai"

_MAX_ATTEMPTS = 3
_BACKOFF_S = 1.0


@runtime_checkable
class BaseEmbedder(Protocol):
    """Turns text into vectors, one vector per input."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; the result has the same order as the input."""
        ...

    @property
    def dim(self) -> int:
        """Vector size, probing the provider on first use when unset (Phase 5)."""
        ...


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider cannot answer after retrying."""


def normalize_vector(vector: Sequence[float]) -> list[float]:
    """Scale ``vector`` to unit length; a zero vector is returned unchanged.

    >>> normalize_vector([3.0, 4.0])
    [0.6, 0.8]
    >>> normalize_vector([0.0, 0.0])
    [0.0, 0.0]

    The zero vector keeps its zeros instead of dividing by zero: it carries no
    direction, and Qdrant's cosine distance treats it as orthogonal to
    everything, which is the honest answer.
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return [float(value) for value in vector]
    return [float(value) / norm for value in vector]


class OpenAICompatEmbedder:
    """Embeds text through an OpenAI-compatible ``/embeddings`` endpoint.

    Credentials are resolved on the first request (like
    ``src/myagent/models/openai_compat.py:70``), so constructing the embedder —
    and therefore assembling the framework — needs no API key. ``client`` is
    injectable so tests run the whole batch/retry path offline.
    """

    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        client: AsyncOpenAI | None = None,
        normalize: bool = False,
    ) -> None:
        self._settings = settings
        self._client = client
        self._normalize = normalize
        self._observed_dim: int | None = None

    @property
    def settings(self) -> EmbeddingSettings:
        """The settings this embedder was built from."""
        return self._settings

    @property
    def normalized(self) -> bool:
        """Whether every vector is scaled to unit length (PLAN 5.4)."""
        return self._normalize

    @property
    def client(self) -> AsyncOpenAI:
        """The OpenAI client, built lazily from the embedding settings."""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._settings.require_api_key(),
                base_url=self._settings.resolved_base_url(),
            )
        return self._client

    @property
    def dim(self) -> int:
        """Vector size: ``EMBED_DIM`` when set, otherwise the last observed size.

        Raising when neither is known is deliberate: a wrong dimension would
        create a Qdrant collection the provider can never fill, so the caller is
        forced to embed at least once (which is what the write path does anyway).
        """
        if self._settings.dim is not None:
            return self._settings.dim
        if self._observed_dim is not None:
            return self._observed_dim
        raise EmbeddingError(
            "EMBED_DIM is unset and no embedding has been requested yet; "
            "call embed() first or set EMBED_DIM"
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts`` in batches, keeping the input order."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for batch in _batches(texts, self._settings.batch_size):
            response = await self._request(batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([float(value) for value in item.embedding] for item in ordered)
        if vectors:
            self._observed_dim = len(vectors[0])
        if self._normalize:
            return [normalize_vector(vector) for vector in vectors]
        return vectors

    async def _request(self, batch: Sequence[str]) -> CreateEmbeddingResponse:
        """Send one batch, retrying transient failures with exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await self.client.embeddings.create(
                    model=self._settings.model_name, input=list(batch)
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "embedding attempt %d/%d failed (%d texts): %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    len(batch),
                    exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_S * 2 ** (attempt - 1))
        raise EmbeddingError(
            f"could not embed {len(batch)} text(s) with model {self._settings.model_name!r} "
            f"after {_MAX_ATTEMPTS} attempts: {last_error}"
        )


class DashScopeEmbedder(OpenAICompatEmbedder):
    """The default RAG embedder (ADR-0005, PLAN 5.4): DashScope's compatible mode.

    ``model_type`` is forced to ``dashscope`` so the class name and the endpoint
    can never disagree: with no ``EMBED_BASE_URL`` the transport will call
    ``https://dashscope.aliyuncs.com/compatible-mode/v1`` and will accept
    ``DASHSCOPE_API_KEY`` as the fallback credential
    (``src/myagent/config/settings.py:36``). Everything else — model name, key,
    dimension — still comes from the settings.
    """

    def __init__(
        self,
        settings: EmbeddingSettings | None = None,
        *,
        client: AsyncOpenAI | None = None,
        normalize: bool = True,
    ) -> None:
        resolved = settings if settings is not None else EmbeddingSettings()
        super().__init__(
            replace(resolved, model_type=DASHSCOPE), client=client, normalize=normalize
        )


class OpenAIEmbedder(OpenAICompatEmbedder):
    """The comparison baseline of PLAN 5.4: OpenAI's own ``/embeddings``.

    Identical to :class:`DashScopeEmbedder` except for the provider it pins, so
    Phase 8 can swap ``EMBED_MODEL_TYPE`` and measure the difference without
    touching a line of pipeline code.
    """

    def __init__(
        self,
        settings: EmbeddingSettings | None = None,
        *,
        client: AsyncOpenAI | None = None,
        normalize: bool = True,
    ) -> None:
        resolved = settings if settings is not None else EmbeddingSettings()
        super().__init__(replace(resolved, model_type=OPENAI), client=client, normalize=normalize)


def build_embedder(
    settings: EmbeddingSettings, *, client: AsyncOpenAI | None = None
) -> OpenAICompatEmbedder:
    """The embedder ``EMBED_MODEL_TYPE`` asks for (PLAN 5.4).

    ``EmbeddingSettings`` has already validated the type against
    ``SUPPORTED_EMBED_MODEL_TYPES``, so this dispatch cannot fail — there is no
    branch to leave untested.
    """
    providers = {DASHSCOPE: DashScopeEmbedder, OPENAI: OpenAIEmbedder}
    return providers[settings.model_type](settings, client=client)


def _batches(texts: Sequence[str], size: int) -> list[list[str]]:
    """Split ``texts`` into chunks of at most ``size`` items."""
    return [list(texts[index : index + size]) for index in range(0, len(texts), size)]
