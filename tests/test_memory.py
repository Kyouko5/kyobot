"""The V1 file-backed memory store."""

from __future__ import annotations

from myagent.memory.base import MemoryRecord
from myagent.memory.store import FileMemoryStore


def test_records_round_trip_through_the_stored_form():
    record = MemoryRecord(
        id="1",
        content="papers live in workspace/",
        created_at="2026-09-20T00:00:00Z",
        tags=("rag",),
        source="notes",
    )

    restored = MemoryRecord.from_dict(record.to_dict())

    assert restored == record
    assert restored.tags == ("rag",)
    assert restored.source == "notes"


def test_missing_optional_fields_default_cleanly():
    restored = MemoryRecord.from_dict({"id": "1", "content": "note"})

    assert restored.created_at == ""
    assert restored.tags == ()
    assert restored.source is None


def test_add_search_and_all(tmp_path):
    store = FileMemoryStore(tmp_path / "memory.jsonl")

    first = store.add("Attention is all you need", tags=["paper"])
    store.add("Qdrant runs locally on port 6333")

    assert store.search("attention") == [first]
    assert store.search("port")[0].content.startswith("Qdrant")
    assert [record.content for record in store.all()] == [
        "Attention is all you need",
        "Qdrant runs locally on port 6333",
    ]
    assert first.tags == ("paper",)
    assert store.path == tmp_path / "memory.jsonl"


def test_search_respects_the_limit(tmp_path):
    store = FileMemoryStore(tmp_path / "memory.jsonl")
    for index in range(3):
        store.add(f"hit {index}")

    assert len(store.search("hit", limit=2)) == 2


def test_an_empty_store_searches_to_nothing(tmp_path):
    store = FileMemoryStore(tmp_path / "absent.jsonl")

    assert store.all() == []
    assert store.search("anything") == []


def test_blank_lines_are_ignored(tmp_path):
    store = FileMemoryStore(tmp_path / "memory.jsonl")
    store.add("kept")

    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    assert [record.content for record in store.all()] == ["kept"]


def test_clear_removes_the_file(tmp_path):
    store = FileMemoryStore(tmp_path / "memory.jsonl")
    store.add("something")

    store.clear()
    store.clear()

    assert store.all() == []
