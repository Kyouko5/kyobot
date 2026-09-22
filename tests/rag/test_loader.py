"""Loading .txt / .md / .pdf into a Document (PLAN 5.2). All of it offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from pdf_fixture import (
    FIXTURE,
    FIXTURE_TITLE,
    PAGE_ONE_TEXT,
    PAGE_TWO_TEXT,
    mini_pdf_bytes,
    pdf_bytes,
)

from myagent.rag import loader as loader_module
from myagent.rag.loader import (
    BaseLoader,
    MarkdownLoader,
    PdfLoader,
    TextLoader,
    UnreadableDocumentError,
    UnsupportedFormatError,
    default_loaders,
    load_document,
    normalize_text,
    supported_suffixes,
)
from myagent.rag.types import content_id, digest, heading_at, page_at


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- the shared contract -----------------------------------------------------


def test_the_three_loaders_satisfy_the_protocol_and_claim_their_suffixes(tmp_path):
    text = TextLoader()
    markdown = MarkdownLoader()
    pdf = PdfLoader()

    assert all(isinstance(loader, BaseLoader) for loader in default_loaders())
    assert text.supports(Path("notes.TXT"))  # suffixes are matched case-insensitively
    assert markdown.supports(Path("notes.md"))
    assert markdown.supports(Path("notes.markdown"))
    assert pdf.supports(Path("paper.pdf"))
    assert not text.supports(Path("paper.pdf"))
    assert not pdf.supports(Path("notes.md"))
    assert [type(loader).__name__ for loader in default_loaders()] == [
        "TextLoader",
        "MarkdownLoader",
        "PdfLoader",
    ]


def test_supported_suffixes_lists_every_registered_loader(tmp_path):
    assert supported_suffixes() == ".markdown, .md, .pdf, .txt"
    # A loader without a ``suffixes`` attribute contributes nothing instead of
    # breaking the message.
    assert supported_suffixes([TextLoader(), object()]) == ".txt"


# --- normalization and content addressing ------------------------------------


def test_normalization_is_one_canonical_form():
    assert normalize_text("a\r\n\r\n\r\nb  ") == "a\n\nb"
    assert normalize_text("a\r\n\r\n\r\n  b  ") == "a\n\n  b"  # only trailing space goes
    assert normalize_text("a\rb") == "a\nb"
    assert normalize_text("\n\n  \n") == ""


def test_the_id_is_the_truncated_digest_of_the_normalized_text(tmp_path):
    document = load_document(write(tmp_path, "notes.txt", "hello world\r\n"))

    assert document.text == "hello world"
    assert document.id == content_id("hello world")
    assert document.id == digest("hello world")[:16]
    assert document.metadata["sha256"] == digest("hello world")
    assert document.metadata["format"] == "text"
    assert document.metadata["pages"] == 1
    assert document.metadata["added_at"]
    assert document.title == "notes"


def test_two_saves_of_the_same_content_load_identically(tmp_path):
    first = load_document(write(tmp_path, "a.txt", "one\r\n\r\ntwo\n"))
    second = load_document(write(tmp_path, "b.txt", "one\n\ntwo"))

    assert first.id == second.id
    assert first.text == second.text
    assert first.metadata["sha256"] == second.metadata["sha256"]


# --- markdown -----------------------------------------------------------------


def test_the_markdown_loader_records_the_heading_outline(tmp_path):
    source = "# MyAgent\n\nIntro paragraph.\n\n## Retrieval\n\nChunks are retrieved by cosine.\n"
    document = load_document(write(tmp_path, "notes.md", source))

    headings = document.metadata["headings"]
    assert [(heading["level"], heading["text"]) for heading in headings] == [
        (1, "MyAgent"),
        (2, "Retrieval"),
    ]
    assert document.title == "MyAgent"
    assert document.metadata["format"] == "markdown"
    for heading in headings:
        assert source[heading["offset"] :].startswith("#" * heading["level"])
    assert heading_at(document.metadata, headings[1]["offset"]) == "Retrieval"
    assert heading_at(document.metadata, 0) == "MyAgent"
    assert page_at(document.metadata, 0) is None  # markdown has no pages


def test_a_markdown_file_without_headings_falls_back_to_the_file_name(tmp_path):
    document = load_document(write(tmp_path, "plain.md", "just a paragraph"))

    assert document.metadata["headings"] == []
    assert document.title == "plain"
    assert heading_at(document.metadata, 0) is None
    assert heading_at({"headings": [{"level": 1, "text": "later", "offset": 5}]}, 0) is None


# --- pdf ----------------------------------------------------------------------


def test_the_committed_fixture_matches_its_builder():
    """The binary in tests/rag/fixtures/ is generated, not downloaded."""
    assert FIXTURE.is_file()
    assert FIXTURE.read_bytes() == mini_pdf_bytes()


def test_the_pdf_loader_extracts_the_text_layer_page_by_page(tmp_path):
    document = PdfLoader().load(FIXTURE)

    assert document.metadata["format"] == "pdf"
    assert document.metadata["pages"] == 2
    assert document.title == FIXTURE_TITLE  # the /Info title wins over the file name
    assert PAGE_ONE_TEXT in document.text
    assert PAGE_TWO_TEXT in document.text
    spans = document.metadata["page_spans"]
    assert [span["page"] for span in spans] == [1, 2]
    assert document.text[spans[0]["start"] : spans[0]["end"]] == PAGE_ONE_TEXT
    assert document.text[spans[1]["start"] : spans[1]["end"]] == PAGE_TWO_TEXT
    assert document.text[spans[0]["end"] : spans[1]["start"]] == "\n\n"
    assert page_at(document.metadata, 0) == 1
    assert page_at(document.metadata, spans[1]["start"]) == 2
    assert document.id == content_id(document.text)


def test_the_pdf_loader_uses_the_file_name_when_there_is_no_title(tmp_path):
    path = tmp_path / "untitled.pdf"
    path.write_bytes(pdf_bytes([PAGE_ONE_TEXT]))

    document = PdfLoader().load(path)

    assert document.metadata["pages"] == 1
    assert document.title == "untitled"
    assert page_at(document.metadata, 0) == 1


def test_a_page_without_a_text_layer_is_skipped(tmp_path):
    path = tmp_path / "scanned.pdf"
    path.write_bytes(pdf_bytes(["", PAGE_TWO_TEXT]))

    document = PdfLoader().load(path)

    assert document.metadata["pages"] == 2  # the file has two pages...
    assert [span["page"] for span in document.metadata["page_spans"]] == [2]
    assert document.text == PAGE_TWO_TEXT  # ...but only one page has text
    assert page_at(document.metadata, 0) == 2


def test_the_pdf_loader_translates_parse_failures(tmp_path):
    path = write(tmp_path, "broken.pdf", "this is not a PDF at all")

    with pytest.raises(UnreadableDocumentError, match="could not read the PDF"):
        PdfLoader().load(path)


# --- the dispatcher ----------------------------------------------------------


def test_load_document_rejects_a_missing_file(tmp_path):
    with pytest.raises(UnreadableDocumentError, match="no such file"):
        load_document(tmp_path / "absent.txt")


def test_load_document_rejects_an_unsupported_suffix(tmp_path):
    path = write(tmp_path, "paper.docx", "nope")

    with pytest.raises(UnsupportedFormatError, match=r"no loader for \.docx"):
        load_document(path)


def test_load_document_names_the_formats_it_can_read_when_it_fails(tmp_path):
    path = write(tmp_path, "notes", "no suffix at all")

    with pytest.raises(UnsupportedFormatError) as failure:
        load_document(path)

    assert "a file without a suffix" in str(failure.value)
    assert "supported formats: .markdown, .md, .pdf, .txt" in str(failure.value)


def test_load_document_accepts_an_explicit_loader_list(tmp_path):
    path = write(tmp_path, "notes.txt", "hello")

    document = load_document(path, [TextLoader()])

    assert document.text == "hello"
    with pytest.raises(UnsupportedFormatError, match=r"no loader for \.txt"):
        load_document(path, [MarkdownLoader()])


def test_a_text_file_that_cannot_be_read_is_reported_readably(tmp_path, monkeypatch):
    path = write(tmp_path, "locked.txt", "hello")

    def explode(self, *args: object, **kwargs: object) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", explode)

    with pytest.raises(UnreadableDocumentError, match="could not read"):
        TextLoader().load(path)


def test_the_module_exposes_a_dispatcher_and_a_default_loader_pool():
    assert loader_module.load_document is load_document
    assert len(default_loaders()) == 3
