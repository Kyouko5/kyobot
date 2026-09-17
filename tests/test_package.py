"""The package imports cleanly and exposes the surface it promises."""

from __future__ import annotations

import re

import myagent


def test_version_is_pep440_like():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[.\-+][0-9A-Za-z.\-+]+)?", myagent.__version__)


def test_declared_public_api_resolves():
    for name in myagent.__all__:
        assert hasattr(myagent, name), name


def test_observability_exports_resolve():
    from myagent import observability

    for name in observability.__all__:
        assert hasattr(observability, name), name
