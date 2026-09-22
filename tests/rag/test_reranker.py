"""The two V1 rerankers of PLAN 5.7."""

from __future__ import annotations

import pytest

from myagent.rag.reranker import BaseReranker, IdentityReranker, ScoreReranker
from myagent.rag.types import Chunk, Document, RetrievedChunk


def hit(index: int, score: float) -> RetrievedChunk:
    """One retrieved chunk with just enough identity to assert an order."""
    return RetrievedChunk(
        chunk=Chunk(id=f"doc:{index}", document_id="doc", index=index, text=f"text {index}"),
        score=score,
        document=Document(id="doc", source="paper.md", text="", title="paper"),
    )


CANDIDATES = [hit(0, 0.5), hit(1, 0.9), hit(2, 0.7)]


def test_both_rerankers_are_base_rerankers():
    assert isinstance(IdentityReranker(), BaseReranker)
    assert isinstance(ScoreReranker(), BaseReranker)


async def test_the_identity_reranker_keeps_the_retriever_order():
    kept = await IdentityReranker().rerank("q", list(CANDIDATES), 3)

    assert kept == CANDIDATES
    assert kept is not CANDIDATES  # a copy, so a caller cannot mutate the input


async def test_the_score_reranker_sorts_by_score():
    ranked = await ScoreReranker().rerank("q", list(CANDIDATES), 3)

    assert [entry.chunk.index for entry in ranked] == [1, 2, 0]


async def test_a_tie_breaks_on_the_chunk_id_so_the_order_is_total():
    tied = [hit(2, 0.5), hit(0, 0.5), hit(1, 0.5)]

    ranked = await ScoreReranker().rerank("q", tied, 5)

    assert [entry.chunk.id for entry in ranked] == ["doc:0", "doc:1", "doc:2"]


@pytest.mark.parametrize("reranker", [IdentityReranker(), ScoreReranker()])
async def test_both_rerankers_truncate_and_handle_a_non_positive_top_n(reranker):
    assert len(await reranker.rerank("q", list(CANDIDATES), 2)) == 2
    assert await reranker.rerank("q", list(CANDIDATES), 0) == []
    assert await reranker.rerank("q", [], 5) == []
