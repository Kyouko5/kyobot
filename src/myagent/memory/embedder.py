"""OpenAI-compatible embedder: the concrete :class:`~myagent.rag.embedder.BaseEmbedder`.

Phase 4's retriever needs at least one embedder that actually talks to a
provider — otherwise the memory chain could only ever be exercised with a fake,
and the Phase 4 acceptance experiment (cross-session recall) could not be run.
ADR-0005 picked DashScope, whose ``compatible-mode`` endpoint is the OpenAI
embeddings wire format, so **one** implementation covers DashScope and any other
OpenAI-compatible endpoint; ``EMBED_MODEL_TYPE`` only selects the default base
URL and the fallback API-key variable (``src/myagent/config/settings.py:36``).

Phase 5 reuses this class for chunk embeddings instead of writing a second one.

Operational details:

* **Batching** (16 texts per request): a turn's memories are few, but a document
  ingest is not, and the endpoint rejects oversized batches.
* **Three attempts with exponential backoff**: an embedding call is on the
  critical path of a write, and providers do return transient 5xx/timeouts.
  When the attempts run out the caller gets :class:`EmbeddingError` — the
  retriever degrades to keyword search (PLAN 4.4) instead of failing the turn.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from openai import AsyncOpenAI
from openai.types import CreateEmbeddingResponse

from myagent.config.settings import EmbeddingSettings
from myagent.observability.logging import get_logger

__all__ = ["EmbeddingError", "OpenAICompatEmbedder"]

logger = get_logger(__name__)

_BATCH_SIZE = 16
_MAX_ATTEMPTS = 3
_BACKOFF_S = 1.0


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider cannot answer after retrying."""


class OpenAICompatEmbedder:
    """Embeds text through an OpenAI-compatible ``/embeddings`` endpoint.

    Credentials are resolved on the first request (like
    ``src/myagent/models/openai_compat.py:70``), so constructing the embedder —
    and therefore assembling the framework — needs no API key. ``client`` is
    injectable so tests run the whole batch/retry path offline.
    """

    def __init__(self, settings: EmbeddingSettings, *, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._client = client
        self._observed_dim: int | None = None

    @property
    def settings(self) -> EmbeddingSettings:
        """The settings this embedder was built from."""
        return self._settings

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
        for batch in _batches(texts, _BATCH_SIZE):
            response = await self._request(batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([float(value) for value in item.embedding] for item in ordered)
        if vectors:
            self._observed_dim = len(vectors[0])
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


def _batches(texts: Sequence[str], size: int) -> list[list[str]]:
    """Split ``texts`` into chunks of at most ``size`` items."""
    return [list(texts[index : index + size]) for index in range(0, len(texts), size)]
