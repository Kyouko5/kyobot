"""The gateway's single long-lived event loop, running in its own thread.

Why this exists at all: :class:`~myagent.agent.loop.AgentLoop` keeps one
``asyncio.Lock`` per session (``src/myagent/agent/loop.py:320``) so that two
turns of the same conversation never interleave, and an ``asyncio`` lock binds
itself to the loop that first awaits it. A blocking HTTP thread that called
``asyncio.run()`` per request would create a **new** loop every time, so the
second message of a session would raise "bound to a different event loop".

One loop for the whole server fixes that and buys the concurrency the locks were
written for: turns of *different* sessions run in parallel, turns of the same
session queue up. Requests submit a coroutine factory and block on the result —
the HTTP layer stays synchronous, which is what ``http.server`` offers.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from myagent.agent.loop import AgentLoop, TurnContext

__all__ = ["ChatRunner"]

_T = TypeVar("_T")

_START_TIMEOUT_S = 10.0
"""How long :meth:`ChatRunner.start` waits for the thread to publish its loop."""
_JOIN_TIMEOUT_S = 10.0
"""How long :meth:`ChatRunner.close` waits for the thread to finish."""
_THREAD_NAME = "myagent-gateway-loop"


class ChatRunner:
    """Runs coroutines on one private event loop, in one daemon thread."""

    def __init__(self, agent: AgentLoop) -> None:
        self._agent = agent
        self._thread: threading.Thread | None = None
        self._handshake: concurrent.futures.Future[asyncio.AbstractEventLoop] | None = None
        self._closed = False
        self._lock = threading.Lock()

    def start(self) -> asyncio.AbstractEventLoop:
        """Start the thread on first use and return its loop."""
        with self._lock:
            if self._closed:
                raise RuntimeError("this ChatRunner is closed")
            if self._handshake is None:
                self._handshake = concurrent.futures.Future()
                self._thread = threading.Thread(
                    target=self._serve, args=(self._handshake,), name=_THREAD_NAME, daemon=True
                )
                self._thread.start()
            handshake = self._handshake
        return handshake.result(timeout=_START_TIMEOUT_S)

    def submit(
        self, factory: Callable[[], Coroutine[Any, Any, _T]]
    ) -> concurrent.futures.Future[_T]:
        """Schedule one coroutine for immediate execution and return its future.

        The factory itself is called before the loop is started, so a closed
        runner refuses the work without leaving an un-awaited coroutine behind;
        the coroutine *body* still runs on the loop thread.
        """
        loop = self.start()
        return asyncio.run_coroutine_threadsafe(factory(), loop)

    def call(self, factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
        """Run one coroutine on the gateway loop and wait for its result."""
        return self.submit(factory).result()

    def turn(self, text: str, session_key: str) -> TurnContext:
        """Run one agent turn with the loop this runner owns."""
        agent = self._agent
        return self.call(lambda: agent.run_turn(text, session_key))

    def close(self) -> None:
        """Stop the loop and join the thread; safe to call more than once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handshake = self._handshake
            thread = self._thread
        if handshake is None or thread is None:
            return  # never started: there is no loop to stop
        loop = handshake.result(timeout=_START_TIMEOUT_S)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=_JOIN_TIMEOUT_S)

    def _serve(self, handshake: concurrent.futures.Future[asyncio.AbstractEventLoop]) -> None:
        """The thread body: publish the loop, then run it until :meth:`close`."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        handshake.set_result(loop)
        try:
            loop.run_forever()
        finally:
            _cancel_pending(loop)
            loop.close()


def _cancel_pending(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel whatever a client left in flight so the loop closes without warnings."""
    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
