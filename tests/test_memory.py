"""The V1 file-backed memory store."""

from __future__ import annotations

from myagent.memory.base import BaseMemory, MemoryRecord
from myagent.memory.store import FileMemoryStore


def test_records_round_trip_through_the_stored_form():
    record = MemoryRecord(
        id="1",
        text="papers live in workspace/",
        created_at="2026-09-20T00:00:00Z",
        kind="semantic",
        tags=("rag",),
        source="notes",
    )

    restored = MemoryRecord.from_dict(record.to_dict())

    assert restored == record
    assert restored.kind == "semantic"
    assert restored.tags == ("rag",)
    assert restored.source == "notes"


def test_missing_optional_fields_default_cleanly():
    restored = MemoryRecord.from_dict({"id": "1", "text": "note"})

    assert restored.created_at == ""
    assert restored.kind == "episodic"
    assert restored.tags == ()
    assert restored.source is None


def test_create_fills_in_the_identity_and_the_timestamp():
    record = MemoryRecord.create("prefers Python", kind="semantic", tags=["preference"])

    assert len(record.id) == 32
    assert record.created_at.endswith("+00:00")
    assert record.kind == "semantic"
    assert record.tags == ("preference",)


def test_add_search_and_all(tmp_path):
    store = FileMemoryStore(tmp_path / "memory.jsonl")

    first = store.add(MemoryRecord.create("Attention is all you need", tags=["paper"]))
    store.add(MemoryRecord.create("Qdrant runs locally on port 6333", kind="semantic"))

    assert store.search("attention") == [first]
    assert store.search("port")[0].text.startswith("Qdrant")
    assert [record.text for record in store.all()] == [
        "Attention is all you need",
        "Qdrant runs locally on port 6333",
    ]
    assert first.tags == ("paper",)
    assert store.path == tmp_path / "memory.jsonl"
    assert isinstance(store, BaseMemory)


def test_search_filters_by_kind_and_respects_the_limit(tmp_path):
    store = FileMemoryStore(tmp_path / "memory.jsonl")
    for index in range(3):
        store.add(MemoryRecord.create(f"hit {index}"))
    store.add(MemoryRecord.create("hit semantic", kind="semantic"))

    assert len(store.search("hit", top_k=2)) == 2
    assert [record.text for record in store.search("hit", kind="semantic")] == ["hit semantic"]


def test_an_empty_store_searches_to_nothing(tmp_path):
    store = FileMemoryStore(tmp_path / "absent.jsonl")

    assert store.all() == []
    assert store.search("anything") == []


def test_blank_lines_are_ignored(tmp_path):
    store = FileMemoryStore(tmp_path / "memory.jsonl")
    store.add(MemoryRecord.create("kept"))

    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    assert [record.text for record in store.all()] == ["kept"]


def test_clear_removes_the_file(tmp_path):
    store = FileMemoryStore(tmp_path / "memory.jsonl")
    store.add(MemoryRecord.create("something"))

    store.clear()
    store.clear()

    assert store.all() == []
