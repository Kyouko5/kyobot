"""``myagent/gateway/runner.py``: one event loop, reused by every request.

The behaviour worth pinning is not "coroutines run" — it is that they run on the
*same* loop every time. ``AgentLoop`` keeps one ``asyncio.Lock`` per session
(``src/myagent/agent/loop.py:320``) and a lock binds to the loop that first
awaits it, so a per-request loop would break the second message of a session.

Named ``test_chat_runner`` rather than ``test_runner``: ``tests/test_runner.py``
already covers the agent's tool loop, and modules without an ``__init__.py`` are
collected by basename.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from myagent.gateway.runner import ChatRunner


class FakeAgent:
    """A stand-in for ``AgentLoop``: the runner only calls ``run_turn``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run_turn(self, text: str, session_key: str) -> str:
        self.calls.append((text, session_key))
        return f"{session_key}: {text}"


async def _loop_id() -> int:
    return id(asyncio.get_running_loop())


def test_a_call_returns_the_coroutine_result():
    runner = ChatRunner(FakeAgent())
    try:
        assert runner.call(lambda: asyncio.sleep(0, result="ok")) == "ok"
    finally:
        runner.close()


def test_every_call_runs_on_the_same_loop():
    runner = ChatRunner(FakeAgent())
    try:
        first = runner.call(_loop_id)
        second = runner.call(_loop_id)
    finally:
        runner.close()

    assert first == second


def test_a_turn_runs_the_agent_the_runner_owns():
    agent = FakeAgent()
    runner = ChatRunner(agent)
    try:
        assert runner.turn("hello", "web:default") == "web:default: hello"
    finally:
        runner.close()

    assert agent.calls == [("hello", "web:default")]


def test_two_threads_are_served_by_the_same_running_loop():
    runner = ChatRunner(FakeAgent())
    results: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def ask(name: str) -> None:
        barrier.wait(timeout=5)
        results[name] = runner.call(_loop_id)

    threads = [threading.Thread(target=ask, args=(name,)) for name in ("a", "b")]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        runner.close()

    assert results["a"] == results["b"]


def test_closing_a_runner_that_never_started_is_harmless():
    ChatRunner(FakeAgent()).close()  # nothing to stop, and no error


def test_closing_twice_is_harmless():
    runner = ChatRunner(FakeAgent())
    runner.call(lambda: asyncio.sleep(0, result=None))

    runner.close()
    runner.close()


def test_a_closed_runner_refuses_to_start_again():
    runner = ChatRunner(FakeAgent())
    runner.call(lambda: asyncio.sleep(0, result=None))
    runner.close()

    with pytest.raises(RuntimeError, match="closed"):
        runner.call(lambda: asyncio.sleep(0, result=None))


def test_closing_cancels_what_is_still_in_flight():
    runner = ChatRunner(FakeAgent())
    never = asyncio.Event()
    future = runner.submit(lambda: never.wait())
    assert not future.done()

    started = time.monotonic()
    runner.close()
    elapsed = time.monotonic() - started

    assert future.cancelled()
    assert elapsed < 5.0
