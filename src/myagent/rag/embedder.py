"""The embedding contract (PLAN 5.4).

Phase 3 defines the interface; Phase 5 implements ``DashScopeEmbedder`` (the
default, ADR-0005) and ``OpenAIEmbedder`` (the comparison baseline).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["BaseEmbedder"]


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
