"""One token estimator for the whole framework.

Everything that needs "roughly how big is this text" calls this function — the
context manager's budget check (Phase 3), the chunk metadata of the RAG
pipeline (Phase 5) and the context budget (Phase 6) — so the numbers stay
comparable across modules.

The heuristic is provider-free on purpose: one token per CJK character and one
token per four other characters, which is close to what the tokenizers behind
the OpenAI-compatible endpoints (DashScope / DeepSeek / OpenAI) produce for
mixed Chinese/English text. It is an *estimate*: Phase 6 compares it against the
``usage`` the provider reports.
"""

from __future__ import annotations

import math

__all__ = ["estimate_tokens"]

# Latin text is ~4 characters per token; CJK text is ~1 token per character.
_CHARS_PER_TOKEN = 4


def _is_cjk(char: str) -> bool:
    """Whether a character is CJK (including punctuation and fullwidth forms)."""
    code = ord(char)
    return (
        0x3000 <= code <= 0x303F  # CJK punctuation
        or 0x3040 <= code <= 0x30FF  # kana
        or 0x3400 <= code <= 0x4DBF  # CJK extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK unified ideographs
        or 0xFF00 <= code <= 0xFFEF  # fullwidth forms
    )


def estimate_tokens(text: str) -> int:
    """Estimate how many tokens ``text`` costs.

    >>> estimate_tokens("")
    0
    >>> estimate_tokens("abcd")
    1
    >>> estimate_tokens("你好")
    2
    """
    cjk_chars = sum(1 for char in text if _is_cjk(char))
    other_chars = len(text) - cjk_chars
    return cjk_chars + math.ceil(other_chars / _CHARS_PER_TOKEN)
