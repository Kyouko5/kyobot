"""Turning a file into a :class:`~myagent.rag.types.Document` (PLAN 5.2).

```text
Document ← Loader ← .txt / .md / .pdf
```

One loader per format, one dispatcher (:func:`load_document`) that picks the
first loader claiming the path. The contract is two methods — ``supports`` and
``load`` — so adding a format is adding a class, not editing a branch
(``docs/decision-records/0007-framework-extension-points.md``).

Two decisions worth stating because they are invisible in the code:

* **One normalization for every format** (:func:`normalize_text`): line endings
  become ``\\n``, trailing whitespace disappears and runs of blank lines collapse.
  The content id of PLAN 5.1 is the hash of *that* text, so re-saving a file with
  different line endings does not create a second document.
* **Page numbers are the loader's job.** The chunker must be able to answer
  "which page is this chunk from" without knowing what a PDF is, so the PDF
  loader records ``metadata["page_spans"]`` (offsets into the normalized text)
  and :func:`myagent.rag.types.page_at` turns an offset into a page number.
  Markdown does the same for headings (``metadata["headings"]``).

Explicit non-goals (PLAN 5.2): no OCR, no scanned pages, no table reconstruction,
no formula extraction. A PDF that carries no text layer loads as an empty
document, and ingest stores it as such rather than inventing content.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pypdf import PdfReader

from myagent.rag.types import Document, content_id, digest, utcnow

__all__ = [
    "BaseLoader",
    "LoaderError",
    "MarkdownLoader",
    "PdfLoader",
    "TextLoader",
    "UnreadableDocumentError",
    "UnsupportedFormatError",
    "default_loaders",
    "load_document",
    "normalize_text",
]

_BLANK_LINES = re.compile(r"\n{3,}")
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
_PAGE_SEPARATOR = "\n\n"


class LoaderError(RuntimeError):
    """Base class for the two ways loading a document can fail."""


class UnsupportedFormatError(LoaderError):
    """Raised when no registered loader claims the file's suffix."""


class UnreadableDocumentError(LoaderError):
    """Raised when the file is missing or its format cannot be parsed."""


@runtime_checkable
class BaseLoader(Protocol):
    """Reads one file format into a :class:`~myagent.rag.types.Document`."""

    def supports(self, path: Path) -> bool:
        """Whether this loader can read ``path`` (by suffix, not by content)."""
        ...

    def load(self, path: Path) -> Document:
        """Read ``path``; the normalized text becomes the document's content address."""
        ...


def normalize_text(text: str) -> str:
    """The one text normalization of the pipeline (see the module docstring).

    >>> normalize_text("a\\r\\n\\r\\n\\r\\nb  ")
    'a\\n\\nb'
    """
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in unified.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


class TextLoader:
    """``.txt``: the whole file is one page-less text."""

    suffixes = (".txt",)

    def supports(self, path: Path) -> bool:
        """Whether ``path`` ends in ``.txt``."""
        return path.suffix.lower() in self.suffixes

    def load(self, path: Path) -> Document:
        """Read the file, normalizing its text and hashing the result."""
        text = normalize_text(_read_text(path))
        return Document(
            id=content_id(text),
            source=str(path),
            text=text,
            title=path.stem,
            metadata=_metadata("text", pages=1, text=text),
        )


class MarkdownLoader:
    """``.md``: like :class:`TextLoader`, plus the heading structure.

    Headings are recorded as ``{"level", "text", "offset"}``. Only ATX headings
    (``# title``) are recognized: Setext headings (``title`` underlined with
    ``===``) and headings inside fenced code blocks are not, because recognizing
    them means parsing Markdown properly and the chunker only needs "which
    section am I in" — PLAN 5.2 keeps this loader dependency-free.
    """

    suffixes = (".md", ".markdown")

    def supports(self, path: Path) -> bool:
        """Whether ``path`` ends in ``.md`` or ``.markdown``."""
        return path.suffix.lower() in self.suffixes

    def load(self, path: Path) -> Document:
        """Read the file and record its heading outline."""
        text = normalize_text(_read_text(path))
        headings = _headings(text)
        return Document(
            id=content_id(text),
            source=str(path),
            text=text,
            title=headings[0]["text"] if headings else path.stem,
            metadata=_metadata("markdown", pages=1, text=text, headings=headings),
        )


class PdfLoader:
    """``.pdf`` through ``pypdf`` (the Phase 5 dependency of ADR-0009).

    Every page becomes a paragraph of one continuous text, and ``page_spans``
    remembers where each page starts and ends — that is what lets a chunk carry
    ``metadata["page"]`` and a citation point back at the page a reader can open.
    """

    suffixes = (".pdf",)

    def supports(self, path: Path) -> bool:
        """Whether ``path`` ends in ``.pdf``."""
        return path.suffix.lower() in self.suffixes

    def load(self, path: Path) -> Document:
        """Extract the text layer of every page.

        Raises:
            UnreadableDocumentError: If pypdf cannot parse the file (a corrupt
                download, an encrypted document, a file that is not a PDF).
        """
        reader = _open_pdf(path)
        parts: list[str] = []
        spans: list[dict[str, Any]] = []
        offset = 0
        for number, page in enumerate(reader.pages, start=1):
            text = normalize_text(page.extract_text() or "")
            if not text:  # a cover page or an image-only page: nothing to index
                continue
            if parts:
                offset += len(_PAGE_SEPARATOR)
            parts.append(text)
            spans.append({"page": number, "start": offset, "end": offset + len(text)})
            offset += len(text)
        # Pages are normalized individually and joined with a blank line, which is
        # exactly what normalizing the whole document would produce.
        text = _PAGE_SEPARATOR.join(parts)
        return Document(
            id=content_id(text),
            source=str(path),
            text=text,
            title=_pdf_title(reader) or path.stem,
            metadata=_metadata("pdf", pages=len(reader.pages), text=text, page_spans=spans),
        )


def default_loaders() -> list[BaseLoader]:
    """The loaders :func:`load_document` dispatches to, in order."""
    return [TextLoader(), MarkdownLoader(), PdfLoader()]


def load_document(path: Path, loaders: Sequence[BaseLoader] | None = None) -> Document:
    """Load ``path`` with the first loader that supports it.

    Args:
        path: The file to read.
        loaders: Override the registered loaders (tests do this).

    Raises:
        UnreadableDocumentError: If ``path`` is not an existing file.
        UnsupportedFormatError: If no loader claims the suffix.
    """
    if not path.is_file():
        raise UnreadableDocumentError(f"no such file: {path}")
    for loader in loaders if loaders is not None else default_loaders():
        if loader.supports(path):
            return loader.load(path)
    raise UnsupportedFormatError(
        f"no loader for {path.suffix or 'a file without a suffix'} ({path}); "
        f"supported formats: {supported_suffixes(loaders)}"
    )


def supported_suffixes(loaders: Sequence[BaseLoader] | None = None) -> str:
    """The suffixes the registered loaders accept, for error messages and help text."""
    registered = loaders if loaders is not None else default_loaders()
    suffixes = {suffix for loader in registered for suffix in getattr(loader, "suffixes", ())}
    return ", ".join(sorted(suffixes))


def _metadata(format_name: str, *, pages: int, text: str, **extra: object) -> dict[str, Any]:
    """The PLAN 5.1 document metadata: format / pages / sha256 / added_at."""
    return {
        "format": format_name,
        "pages": pages,
        "sha256": digest(text),
        "added_at": utcnow().isoformat(timespec="seconds"),
        **extra,
    }


def _read_text(path: Path) -> str:
    """Read a text file, replacing undecodable bytes instead of failing."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # a directory, a permission bit, a racing deletion
        raise UnreadableDocumentError(f"could not read {path}: {exc}") from exc


def _open_pdf(path: Path) -> PdfReader:
    """Open a PDF, translating every pypdf failure into one readable error."""
    try:
        return PdfReader(str(path))
    except Exception as exc:
        raise UnreadableDocumentError(f"could not read the PDF {path}: {exc}") from exc


def _pdf_title(reader: PdfReader) -> str | None:
    """The ``/Title`` the PDF carries, when it carries one."""
    metadata = reader.metadata
    title = getattr(metadata, "title", None) if metadata is not None else None
    return str(title).strip() if title else None


def _headings(text: str) -> list[dict[str, Any]]:
    """Every ATX heading of ``text`` with the offset it starts at."""
    headings: list[dict[str, Any]] = []
    offset = 0
    for line in text.split("\n"):
        match = _ATX_HEADING.match(line)
        if match:
            headings.append(
                {"level": len(match.group(1)), "text": match.group(2).strip(), "offset": offset}
            )
        offset += len(line) + 1
    return headings
