"""Static assets, addressed by plain file name.

The UI is four files on disk (``index.html``, ``app.js``, ``style.css``,
``favicon.svg``) served straight from the package. That is only safe because the
lookup is a *whitelist of names*, not a path join: a request may name a file that
sits directly in the assets directory and nothing else, so no amount of
percent-encoding can walk out of the package (``tests/gateway/test_assets.py``
feeds it ``../``, absolute paths and separators).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from myagent.gateway.errors import GatewayError

__all__ = ["ASSETS_DIR", "Assets"]

ASSETS_DIR: Final = Path(__file__).parent / "assets"

#: Suffix → media type. Explicit instead of ``mimetypes.guess_type`` so the
#: served types are the ones this project promises (and charset-tagged).
CONTENT_TYPES: Final = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}
_FALLBACK_CONTENT_TYPE: Final = "application/octet-stream"


class Assets:
    """Reads the UI files, refusing anything that is not a flat file name."""

    def __init__(self, directory: Path = ASSETS_DIR) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        """The directory the UI files are read from."""
        return self._directory

    def names(self) -> list[str]:
        """Every servable file, sorted (the completeness check in the tests)."""
        return sorted(path.name for path in self._directory.iterdir() if path.is_file())

    def path_for(self, name: str) -> Path:
        """Resolve one asset name, or raise 404.

        Raises:
            GatewayError: 404 for a name with a separator, a leading dot or a
                ``..`` segment, and for a name that is simply not there.
        """
        if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
            raise GatewayError(404, f"unknown asset: {name!r}")
        candidate = self._directory / name
        if not candidate.is_file():
            raise GatewayError(404, f"unknown asset: {name!r}")
        return candidate

    def read(self, name: str) -> bytes:
        """The bytes of one asset (text files are UTF-8 on disk)."""
        return self.path_for(name).read_bytes()

    def content_type(self, name: str) -> str:
        """The media type for an asset name, by suffix."""
        return CONTENT_TYPES.get(Path(name).suffix.lower(), _FALLBACK_CONTENT_TYPE)
