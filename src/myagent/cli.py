"""Command line entry point.

    myagent chat -m "现在几点"    # one message
    myagent chat                  # interactive (/exit, /session, /clear)
    myagent tools                 # list the registered tools

Standard library ``argparse`` only: the framework keeps its runtime dependency
list at one entry (``openai``, ADR-0006) and the CLI is thin enough not to need
a framework. Assembly lives in :func:`build_agent_loop`; Phase 3 lifts it into
``runtime.py`` once the pieces are decoupled.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from myagent.agent.context import ContextBuilder
from myagent.agent.loop import AgentLoop, MessageBus
from myagent.config.env import MissingEnvError
from myagent.config.settings import AgentSettings, LLMSettings
from myagent.models.openai_compat import OpenAICompatModel
from myagent.observability.logging import configure_logging
from myagent.session.manager import DEFAULT_SESSION_KEY, SessionManager
from myagent.tools.builtin import build_default_registry

__all__ = ["build_agent_loop", "main"]

_PROMPT = "you> "
_ANSWER_PREFIX = "agent> "
_COMMANDS = ("/exit", "/quit", "/session", "/clear")


def build_agent_loop(*, bus: MessageBus | None = None) -> AgentLoop:
    """Assemble the runtime from the environment (the Phase 2 wiring point)."""
    agent_settings = AgentSettings.from_env()
    llm_settings = LLMSettings.from_env()
    return AgentLoop(
        model=OpenAICompatModel(llm_settings),
        tools=build_default_registry(agent_settings),
        context=ContextBuilder(agent_settings.workspace),
        sessions=SessionManager.from_settings(agent_settings),
        settings=agent_settings,
        bus=bus,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the requested command."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging()

    if args.command == "tools":
        return _list_tools()
    return _chat(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="myagent", description="MyAgent command line")
    subcommands = parser.add_subparsers(dest="command", required=True)

    chat = subcommands.add_parser("chat", help="talk to the agent")
    chat.add_argument("-m", "--message", help="send one message and exit")
    chat.add_argument(
        "-s",
        "--session",
        default=DEFAULT_SESSION_KEY,
        help=f"session key to store the conversation under (default {DEFAULT_SESSION_KEY})",
    )

    subcommands.add_parser("tools", help="list the registered tools")
    return parser


def _list_tools() -> int:
    """Print the registered tools; needs no credentials, so it works offline."""
    tools = build_default_registry(AgentSettings.from_env())
    for name in sorted(tools.tool_names):
        tool = tools.get(name)
        assert tool is not None  # names come from the registry itself
        mode = "read-only" if tool.read_only else "writes"
        parameters = ", ".join(tool.parameters.get("properties", {})) or "no parameters"
        print(f"{name} ({mode})\n    {tool.description}\n    parameters: {parameters}")
    return 0


def _chat(args: argparse.Namespace) -> int:
    llm_settings = LLMSettings.from_env()
    try:
        llm_settings.require_model()
        llm_settings.require_api_key()
    except MissingEnvError as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 2

    loop = build_agent_loop()
    if args.message:
        print(asyncio.run(loop.run_once(args.message, args.session)))
        return 0
    return asyncio.run(_interactive(loop, args.session))


async def _interactive(loop: AgentLoop, session_key: str) -> int:
    """Read lines from stdin until EOF or ``/exit``.

    ``/session`` shows the key, ``/clear`` forgets the transcript. Commands are
    handled here rather than in a loop stage: upstream routes them before the
    model call for the same reason - they must work even while a turn is broken.
    """
    print(f"session: {session_key} (commands: /exit, /session, /clear)")
    while True:
        try:
            line = await asyncio.to_thread(input, _PROMPT)
        except EOFError:
            print()
            return 0
        text = line.strip()
        if not text:
            continue
        if text in ("/exit", "/quit"):
            return 0
        if text == "/session":
            print(f"session: {session_key}")
            continue
        if text == "/clear":
            loop.sessions.clear(session_key)
            print("cleared")
            continue
        print(f"{_ANSWER_PREFIX}{await loop.run_once(text, session_key)}")
