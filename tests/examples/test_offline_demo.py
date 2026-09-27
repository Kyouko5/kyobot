"""The committed four-demo harness runs with no service and validates its evidence."""

from __future__ import annotations

import runpy
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2] / "examples" / "research_agent" / "offline_demo.py"


async def test_four_demos_and_refusal_are_reproducible_offline():
    run_demos = runpy.run_path(str(DEMO))["run_demos"]
    report = await run_demos()
    graph_id = report["fixture_ids"]["graph_indexing"]
    dense_id = report["fixture_ids"]["dense_retrieval"]
    single = report["single_paper"]
    comparison = report["comparison"]
    memory = report["cross_session_memory"]
    combined = report["tool_calling"]
    refusal = report["refusal"]

    assert len(single) == 3
    assert all(f"[{graph_id}#" in item["answer"] for item in single)
    assert f"[{graph_id}#" in comparison["answer"]
    assert f"[{dense_id}#" in comparison["answer"]
    assert memory[0]["session"] != memory[1]["session"]
    assert "concise graph comparisons" in memory[1]["answer"]
    assert combined["tool_calls"] == 3
    assert combined["tools_used"] == ["search_paper", "save_note", "search_memory"]
    assert "知识库中没有" in refusal["answer"]
    for item in [*single, comparison, *memory, combined, refusal]:
        assert item["model_rounds"] >= 1
        assert item["estimated_tokens"] > 0
        assert item["latency_ms"] >= 0
