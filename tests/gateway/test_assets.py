"""Serving the UI files without ever leaving the assets directory (PLAN Phase G)."""

from __future__ import annotations

import pytest

from myagent.gateway.assets import ASSETS_DIR, Assets
from myagent.gateway.errors import GatewayError


def test_the_ui_files_are_where_the_package_expects_them():
    assets = Assets()

    assert assets.directory == ASSETS_DIR
    assert assets.names() == ["app.js", "favicon.svg", "index.html", "style.css"]


def test_the_index_page_loads_the_scripts_it_needs():
    html = Assets().read("index.html").decode("utf-8")

    assert "<title>MyAgent</title>" in html
    assert 'src="/static/app.js"' in html
    assert "/static/style.css" in html
    assert "/static/favicon.svg" in html


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("index.html", "text/html; charset=utf-8"),
        ("app.js", "text/javascript; charset=utf-8"),
        ("style.css", "text/css; charset=utf-8"),
        ("favicon.svg", "image/svg+xml"),
    ],
)
def test_every_ui_file_gets_its_own_content_type(name, expected):
    assert Assets().content_type(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "../pyproject.toml",
        "..%2Fpyproject.toml",
        "/etc/passwd",
        "..",
        ".env",
        "app.js/",
        "app\\js",
        "assets/app.js",
        "missing.js",
        "",
    ],
)
def test_a_name_that_is_not_a_flat_asset_file_is_a_404(name):
    with pytest.raises(GatewayError) as error:
        Assets().read(name)

    assert error.value.status == 404


def test_a_directory_is_not_servable(tmp_path):
    (tmp_path / "nested").mkdir()

    with pytest.raises(GatewayError) as error:
        Assets(tmp_path).read("nested")

    assert error.value.status == 404


def test_an_unknown_suffix_is_served_as_octets(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01")
    (tmp_path / "nested").mkdir()  # directories are skipped by names()
    assets = Assets(tmp_path)

    assert assets.names() == ["blob.bin"]
    assert assets.content_type("blob.bin") == "application/octet-stream"
    assert assets.read("blob.bin") == b"\x00\x01"
