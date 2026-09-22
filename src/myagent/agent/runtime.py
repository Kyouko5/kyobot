"""Runtime limits for the agent loop (PLAN 3.2's ``AgentRuntimeConfig``).

Before Phase 3 these numbers lived in ``AgentSettings`` next to paths and
sessions; keeping "how the loop behaves" separate from "where files live" lets
the loop depend on one small value object and lets Phase 6 add the context
budget without touching configuration loading again.

Upstream keeps the same knobs in its configuration layer
(``config/schema.py:129`` carries ``max_tool_iterations`` and
``max_tool_result_chars``).
"""

from __future__ import annotations

from dataclasses import dataclass

from myagent.agent.context import ContextBudget
from myagent.config.settings import (
    DEFAULT_AGENT_MAX_ITERATIONS,
    DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS,
    DEFAULT_AGENT_TOOL_TIMEOUT_S,
    AgentSettings,
    LLMSettings,
)

__all__ = ["AgentRuntimeConfig"]

# Leave room for the answer and for the provider's own framing overhead.
# The formula is PLAN 6.2's: context_window - max_output_tokens - 1024.
_SAFETY_MARGIN_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    """How one turn is allowed to behave."""

    max_iterations: int = DEFAULT_AGENT_MAX_ITERATIONS
    tool_timeout_s: float = DEFAULT_AGENT_TOOL_TIMEOUT_S
    max_tool_result_chars: int = DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS
    context_budget_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.max_iterations <= 0:
            raise ValueError(f"max_iterations must be positive, got {self.max_iterations}")
        if self.tool_timeout_s <= 0:
            raise ValueError(f"tool_timeout_s must be positive, got {self.tool_timeout_s}")
        if self.max_tool_result_chars <= 0:
            raise ValueError(
                f"max_tool_result_chars must be positive, got {self.max_tool_result_chars}"
            )
        if self.context_budget_tokens is not None and self.context_budget_tokens <= 0:
            raise ValueError(
                f"context_budget_tokens must be positive when set, got {self.context_budget_tokens}"
            )

    @property
    def context_budget(self) -> ContextBudget:
        """The Phase 6 budget: this config's input limit plus PLAN 6.2's shares.

        Built here rather than inside ``build()`` so the formula
        ``context_window - max_output_tokens - 1024`` and the per-source ratios
        have exactly one home each (PLAN 6.2's "不写死在 ``build()`` 里").
        """
        return ContextBudget(input_tokens=self.context_budget_tokens)

    @classmethod
    def from_settings(
        cls, agent: AgentSettings, llm: LLMSettings | None = None
    ) -> AgentRuntimeConfig:
        """Build the runtime limits from the loaded settings.

        ``context_budget_tokens`` stays ``None`` when the model settings cannot
        produce a positive budget (a tiny ``LLM_CONTEXT_WINDOW``), which means
        "do not check" rather than "budget of zero".
        """
        return cls(
            max_iterations=agent.max_iterations,
            tool_timeout_s=agent.tool_timeout_s,
            max_tool_result_chars=agent.max_tool_result_chars,
            context_budget_tokens=_context_budget(llm),
        )


def _context_budget(llm: LLMSettings | None) -> int | None:
    """``context_window - max_output_tokens - safety margin`` (PLAN 6.2)."""
    if llm is None:
        return None
    budget = llm.context_window - llm.max_tokens - _SAFETY_MARGIN_TOKENS
    return budget if budget > 0 else None
