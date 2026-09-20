"""The agent loop: one turn of conversation.

    build -> run -> save -> respond

Upstream (``agent/loop.py:1594``) splits a turn into seven stages; V1 keeps four
and folds ``restore`` / ``compact`` into ``build`` (PLAN 2.5). The split still
pays for itself: each stage is timed and logged separately, so a slow or broken
turn is attributable without a debugger.

Responsibilities are deliberately the same as upstream's: the loop owns *who*,
*which context* and *where the result goes*; the runner owns *how many times the
model and the tools talk*.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import uuid4

from myagent.agent.context import ContextBuilder, ContextBundle
from myagent.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from myagent.agent.types import InboundMessage, Message, OutboundMessage, StopReason
from myagent.config.settings import AgentSettings
from myagent.models.base import BaseModel
from myagent.observability.logging import get_logger
from myagent.session.manager import DEFAULT_SESSION_KEY, SessionManager
from myagent.tools.registry import ToolRegistry

__all__ = ["AgentLoop", "MessageBus", "TurnContext"]

logger = get_logger(__name__)

_STAGE_BUILD = "build"
_STAGE_RUN = "run"
_STAGE_SAVE = "save"
_STAGE_RESPOND = "respond"


class MessageBus:
    """Minimal in-process bus: inbound messages in, outbound messages out.

    Upstream's bus fans out to channels, cron and sub-agents; V1 needs exactly
    one producer and one consumer, so an ``asyncio.Queue`` on each side is
    enough. ``close()`` posts a sentinel that makes :meth:`AgentLoop.run` return;
    the outbound side stays open so readers can still drain pending answers.
    """

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[InboundMessage | None] = asyncio.Queue()
        self._outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, message: InboundMessage) -> None:
        """Queue a message for the loop."""
        await self._inbound.put(message)

    async def consume_inbound(self) -> InboundMessage | None:
        """Return the next message, or ``None`` once the bus is closed."""
        return await self._inbound.get()

    async def publish_outbound(self, message: OutboundMessage) -> None:
        """Queue the loop's answer."""
        await self._outbound.put(message)

    async def consume_outbound(self) -> OutboundMessage:
        """Return the next answer (waits until one is published)."""
        return await self._outbound.get()

    async def close(self) -> None:
        """Stop the loop after the messages already queued."""
        await self._inbound.put(None)


@dataclass(slots=True)
class TurnContext:
    """Mutable state passed between the four turn stages."""

    session_key: str
    user_input: str
    turn_id: str = field(default_factory=lambda: uuid4().hex[:8])
    bundle: ContextBundle | None = None
    result: AgentRunResult | None = None
    outbound: OutboundMessage | None = None

    def require_bundle(self) -> ContextBundle:
        """Return the built context, or fail loudly when the stage order broke."""
        if self.bundle is None:
            raise RuntimeError("turn stage order violated: context was not built")
        return self.bundle

    def require_result(self) -> AgentRunResult:
        """Return the run result, or fail loudly when the stage order broke."""
        if self.result is None:
            raise RuntimeError("turn stage order violated: the runner did not run")
        return self.result

    def require_outbound(self) -> OutboundMessage:
        """Return the outbound message, or fail loudly when the stage order broke."""
        if self.outbound is None:
            raise RuntimeError("turn stage order violated: the response was not prepared")
        return self.outbound


class AgentLoop:
    """Ties the model, the tools, the context builder and the session store together."""

    def __init__(
        self,
        *,
        model: BaseModel,
        tools: ToolRegistry,
        context: ContextBuilder,
        sessions: SessionManager,
        settings: AgentSettings,
        bus: MessageBus | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.context = context
        self.sessions = sessions
        self.settings = settings
        self.bus = bus if bus is not None else MessageBus()
        self.runner = AgentRunner()
        self._locks: dict[str, asyncio.Lock] = {}

    async def run_once(self, user_input: str, session_key: str = DEFAULT_SESSION_KEY) -> str:
        """Process one message and return the answer (used by the CLI and tests)."""
        message = InboundMessage(session_key=session_key, content=user_input)
        return (await self._process(message)).content

    async def run(self) -> None:
        """Consume the bus until it is closed."""
        while True:
            message = await self.bus.consume_inbound()
            if message is None:
                return
            await self.bus.publish_outbound(await self._process(message))

    async def _process(self, message: InboundMessage) -> OutboundMessage:
        """Run the four stages under the session lock (serial per session, parallel across)."""
        async with self._session_lock(message.session_key):
            ctx = TurnContext(session_key=message.session_key, user_input=message.content)
            await self._run_stage(ctx, _STAGE_BUILD, self._build_turn)
            await self._run_stage(ctx, _STAGE_RUN, self._run_turn)
            await self._run_stage(ctx, _STAGE_SAVE, self._save_turn)
            await self._run_stage(ctx, _STAGE_RESPOND, self._prepare_outbound)
            return ctx.require_outbound()

    async def _build_turn(self, ctx: TurnContext) -> None:
        """Load the session and assemble the request messages."""
        session = self.sessions.get_or_create(ctx.session_key)
        ctx.bundle = self.context.build(history=session.transcript(), user_input=ctx.user_input)

    async def _run_turn(self, ctx: TurnContext) -> None:
        """Hand the messages to the runner."""
        bundle = ctx.require_bundle()
        ctx.result = await self.runner.run(
            AgentRunSpec(
                messages=list(bundle.messages),
                tools=self.tools,
                model=self.model,
                max_iterations=self.settings.max_iterations,
                max_tool_result_chars=self.settings.max_tool_result_chars,
                tool_timeout_s=self.settings.tool_timeout_s,
            )
        )

    async def _save_turn(self, ctx: TurnContext) -> None:
        """Append this turn's messages to the session.

        Never the system prompt (rebuilt every turn) and never the replayed
        history (already on disk), which is what ``transcript_start`` marks.
        """
        bundle = ctx.require_bundle()
        result = ctx.require_result()
        produced: list[Message] = [
            *bundle.messages[bundle.transcript_start :],
            *result.messages[len(bundle.messages) :],
        ]
        self.sessions.append(ctx.session_key, produced)

    async def _prepare_outbound(self, ctx: TurnContext) -> None:
        """Turn the run result into the text the channel receives."""
        result = ctx.require_result()
        if result.stop_reason is StopReason.ERROR:
            logger.warning("turn %s failed: %s", ctx.turn_id, result.error)
        ctx.outbound = OutboundMessage(
            session_key=ctx.session_key,
            content=_answer_text(result),
            stop_reason=result.stop_reason,
            tools_used=tuple(result.tools_used),
        )

    async def _run_stage(
        self, ctx: TurnContext, name: str, stage: Callable[[TurnContext], Awaitable[None]]
    ) -> None:
        """Run one stage and log its duration (upstream: ``_run_turn_stage``)."""
        started = time.perf_counter()
        await stage(ctx)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.debug("[turn %s] stage %s completed in %.1fms", ctx.turn_id, name, elapsed_ms)

    def _session_lock(self, session_key: str) -> asyncio.Lock:
        lock = self._locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_key] = lock
        return lock


def _answer_text(result: AgentRunResult) -> str:
    """Pick the text for a turn that produced no model content of its own."""
    if result.final_content:
        return result.final_content
    if result.stop_reason is StopReason.ERROR:
        return f"The model call failed: {result.error}"
    if result.stop_reason is StopReason.EMPTY_FINAL_RESPONSE:
        return "The model returned an empty answer; please try rephrasing the request."
    return "Stopped at the tool-iteration limit without a final answer."
