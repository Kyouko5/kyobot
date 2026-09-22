"""PDF fixtures, built byte by byte (the Phase 5 loader fixture).

The loader tests need a PDF with a real text layer, and they need it *offline*
and reproducible: no writer library, no downloaded sample whose provenance
nobody can check. So this module builds the smallest PDF that pypdf extracts
text from — a catalog, a page tree, one Helvetica font, and one page object plus
one content stream per page — and ``tests/rag/fixtures/mini.pdf`` is exactly
``mini_pdf_bytes()`` (``test_the_committed_fixture_matches_its_builder`` asserts
that, so the committed binary cannot drift away from its source).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "mini.pdf"

FIXTURE_TITLE = "MyAgent RAG fixture"
PAGE_ONE_TEXT = (
    "MyAgent RAG fixture, page one. A document is loaded, split into chunks and embedded."
)
PAGE_TWO_TEXT = (
    "MyAgent RAG fixture, page two. Retrieval returns the chunks that answer the question."
)


def _stream(text: str) -> bytes:
    """One page's content stream: set the font, position the cursor, show the text."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode("ascii")


def _page(content_object: int) -> bytes:
    """One page object, pointing at its content stream."""
    return (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_object} 0 R >>"
    ).encode("ascii")


def pdf_bytes(
    pages: Sequence[str] = (PAGE_ONE_TEXT, PAGE_TWO_TEXT), *, title: str | None = None
) -> bytes:
    """Build a PDF with one page per entry of ``pages``.

    Args:
        pages: The text of each page (an empty string makes a page with no text
            layer, which is what a scan looks like to a text extractor).
        title: When given, the document gets an ``/Info`` dictionary with it.
    """
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, filled in once every page object exists
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids: list[str] = []
    for text in pages:
        page_number = len(objects) + 1
        kids.append(f"{page_number} 0 R")
        objects.append(_page(page_number + 1))
        objects.append(_stream(text))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode("ascii")
    info = ""
    if title is not None:
        objects.append(f"<< /Title ({title}) >>".encode("ascii"))
        info = f" /Info {len(objects)} 0 R"

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R{info} >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def mini_pdf_bytes() -> bytes:
    """The bytes of the committed two-page fixture (with a document title)."""
    return pdf_bytes(title=FIXTURE_TITLE)


def write_mini_pdf(path: Path = FIXTURE) -> Path:
    """Write the fixture (used once by hand; the test compares, it does not write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mini_pdf_bytes())
    return path
