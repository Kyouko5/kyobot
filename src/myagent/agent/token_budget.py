"""The one token estimator the context budget measures with (PLAN 6.2).

``myagent.tokens.estimate_tokens`` stays the framework-wide heuristic (1 token
per CJK character, 1 per four others) and the single source of truth: the RAG
chunker stamps it on every chunk (``src/myagent/rag/chunker.py:105``), the
context manager sizes whole sections with it, and Phase 5's ``token_estimate``
metadata is therefore directly comparable to a Phase 6 section report.

This module is the thin layer the *context* code needs on top of it:

* :func:`message_tokens` — one :class:`~myagent.agent.types.Message`, tool calls
  included, so a section made of messages and a section made of text are measured
  with the same ruler;
* :func:`messages_tokens` — a whole message list, which is what the conversation
  quota and every compaction report are expressed in;
* :func:`truncate_to_tokens` — the inverse operation, used when the archived
  summary has to be cut down to its share of the budget;
* :data:`TokenCounter` — the optional provider-side counter
  (``BaseModel.count_tokens``, ``src/myagent/models/base.py:97``). It returns
  ``None`` in V1, and the context manager falls back to the heuristic; when a
  provider does implement it, the real number wins (PLAN 2.2 reserved it for
  exactly this).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from myagent.agent.types import Message
from myagent.tokens import estimate_tokens

__all__ = [
    "TokenCounter",
    "message_tokens",
    "messages_tokens",
    "truncate_to_tokens",
]

TokenCounter = Callable[[Sequence[Message]], "int | None"]
"""Count the tokens of a request, or answer ``None`` when the provider cannot."""


def message_tokens(message: Message) -> int:
    """Estimate one message, including the tool calls it carries.

    The role is counted too: it is a handful of bytes on the wire, and dropping
    it would make a long tool-call batch look cheaper than it is.
    """
    total = estimate_tokens(message.content or "") + estimate_tokens(message.role)
    for call in message.tool_calls:
        total += estimate_tokens(call.name) + estimate_tokens(str(call.arguments))
    return total


def messages_tokens(messages: Sequence[Message]) -> int:
    """Estimate a whole message list (the sum of :func:`message_tokens`)."""
    return sum(message_tokens(message) for message in messages)


def truncate_to_tokens(text: str, limit: int) -> str:
    """Keep the longest prefix of ``text`` that fits ``limit`` tokens.

    ``estimate_tokens`` never decreases when a string grows, so the cut is found
    with a binary search over the prefix length instead of counting character by
    character — the same number, ``O(log n)`` estimates instead of ``O(n)``.

    >>> truncate_to_tokens("abcd", 1)
    'abcd'
    >>> truncate_to_tokens("abcd", 0)
    ''
    """
    if limit <= 0:
        return ""
    if estimate_tokens(text) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return text[:low]
