"""The shared token estimator."""

from __future__ import annotations

import pytest

from myagent.tokens import estimate_tokens


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("abcd", 1),
        ("abcde", 2),
        ("你好", 2),
        # Fullwidth punctuation and letters are CJK: one token each.
        ("你好，世界", 5),  # noqa: RUF001 - the fullwidth comma is the point
        ("hello 你好", 4),
        ("ＡＢ", 2),  # noqa: RUF001 - fullwidth forms are counted as CJK
    ],
)
def test_estimate_tokens(text, expected):
    assert estimate_tokens(text) == expected


def test_the_estimate_grows_with_the_text():
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("字" * 100) == 100
