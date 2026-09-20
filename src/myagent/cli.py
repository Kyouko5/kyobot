"""Command line entry point.

    myagent chat -m "现在几点"    # one message
    myagent chat                  # interactive (/exit, /session, /clear)
    myagent tools                 # list the registered tools
    myagent memory list --kind semantic -n 20
    myagent memory search "我的研究方向" -k 5
    myagent memory add "用户偏好 Python" --kind semantic --importance 0.8
    myagent memory consolidate --dry-run
    myagent memory forget <memory_id>

Standard library ``argparse`` only: the framework keeps its runtime dependency
list at one entry (``openai``, ADR-0006) and the CLI is thin enough not to need
a framework. Since Phase 3 the CLI does no assembly at all: it calls
``myagent.runtime.build_agent()``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from myagent.agent.loop import AgentLoop
from myagent.config.env import MissingEnvError
from myagent.config.settings import LLMSettings, Settings
from myagent.memory.manager import MemoryManager
from myagent.memory.types import KINDS, Kind, MemoryRecord
from myagent.observability.logging import configure_logging
from myagent.runtime import build_agent, build_memory
from myagent.session.base import DEFAULT_SESSION_KEY

__all__ = ["main"]

_PROMPT = "you> "
_ANSWER_PREFIX = "agent> "
_COMMANDS = ("/exit", "/quit", "/session", "/clear")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the requested command."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging()

    if args.command == "tools":
        return _list_tools()
    if args.command == "memory":
        return _memory(args)
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
    _add_memory_parser(subcommands)
    return parser


def _add_memory_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``myagent memory ...``: inspect and maintain long-term memory (PLAN 4.9).

    These commands are the operational half of the memory system: they read what
    the agent decided to remember, delete a wrong memory, and run the
    consolidation the loop deliberately does not do on its own.
    """
    memory = subcommands.add_parser("memory", help="inspect and maintain long-term memory")
    verbs = memory.add_subparsers(dest="memory_command", required=True)

    listing = verbs.add_parser("list", help="show stored memories, newest first")
    listing.add_argument("--kind", choices=KINDS, help="only this memory layer")
    listing.add_argument("-n", "--limit", type=int, default=20, help="how many to show")

    search = verbs.add_parser("search", help="search memories")
    search.add_argument("query", help="what to look for")
    search.add_argument("--kind", choices=KINDS, help="only this memory layer")
    search.add_argument("-k", "--top-k", type=int, default=5, help="how many hits to show")

    add = verbs.add_parser("add", help="write one memory by hand")
    add.add_argument("text", help="the memory itself")
    add.add_argument("--kind", choices=KINDS, default="semantic", help="mem2ory layer (semantic)")
    add.add_argument("--importance", type=float, default=0.8, help="0..1, used when ranking")
    add.add_argument("-s", "--session", default=DEFAULT_SESSION_KEY, help="source session key")

    consolidate = verbs.add_parser("consolidate", help="fold episodic memories into semantic ones")
    consolidate.add_argument(
        "--dry-run", action="store_true", help="show what would be merged, change nothing"
    )

    forget = verbs.add_parser("forget", help="delete one memory by id")
    forget.add_argument("memory_id", help="the id shown by 'memory list'")


def _memory(args: argparse.Namespace) -> int:
    """Dispatch ``myagent memory <verb>``."""
    manager = build_memory(Settings.from_env())
    if args.memory_command == "list":
        return _memory_list(manager, args)
    if args.memory_command == "search":
        return _memory_search(manager, args)
    if args.memory_command == "add":
        return _memory_add(manager, args)
    if args.memory_command == "consolidate":
        return _memory_consolidate(manager, args)
    return _memory_forget(manager, args)


def _memory_list(manager: MemoryManager, args: argparse.Namespace) -> int:
    """Print stored memories."""
    kind: Kind | None = args.kind
    records = manager.all(kind=kind, limit=args.limit)
    if not records:
        print("no memories yet" + (f" of kind {kind}" if kind else ""))
        return 0
    for record in records:
        print(_memory_line(record))
    print(f"({len(records)} shown, {manager.count(kind=kind)} stored)")
    return 0


def _memory_search(manager: MemoryManager, args: argparse.Namespace) -> int:
    """Search memories, reporting degradation instead of hiding it.

    When Qdrant is down the retriever answers from keyword search; the note it
    returns is printed on stderr so the output format stays pipeable (PLAN 4.9:
    "有明确提示" rather than a traceback).
    """
    context = asyncio.run(manager.context(args.query, kind=args.kind, top_k=args.top_k))
    for hit in context.hits:
        print(f"{_memory_line(hit.record)}  score={hit.score:.3f} via={hit.reason}")
    if not context.hits:
        print(f"no memory matched {args.query!r}")
    if context.note:
        print(f"note: {context.note}", file=sys.stderr)
    return 0


def _memory_add(manager: MemoryManager, args: argparse.Namespace) -> int:
    """Write one hand-made memory."""
    if not manager.enabled:
        print(
            "myagent: memory is disabled (MYAGENT_MEMORY_ENABLED=false), nothing was stored",
            file=sys.stderr,
        )
        return 1
    if not 0.0 <= args.importance <= 1.0:
        print("myagent: --importance must be within 0..1", file=sys.stderr)
        return 2
    record = manager.semantic.build(
        args.text, importance=args.importance, session_key=args.session, source="manual"
    )
    if args.kind == "episodic":
        record = manager.episodic.build(
            args.text, importance=args.importance, session_key=args.session, source="manual"
        )
    stored = asyncio.run(manager.write([record]))
    if not stored:
        print("myagent: the memory was not stored", file=sys.stderr)
        return 1
    print(f"stored {record.kind} memory {record.id}")
    return 0


def _memory_consolidate(manager: MemoryManager, args: argparse.Namespace) -> int:
    """Run the Consolidator (PLAN 4.8)."""
    result = asyncio.run(manager.consolidate(dry_run=args.dry_run))
    prefix = "would consolidate" if args.dry_run else "consolidated"
    if not result.pending_before:
        print("nothing to consolidate")
        return 0
    for record in result.created:
        print(f"{prefix} into {record.kind} {record.id}: {record.text}")
    print(f"{prefix} {result.pending_before} episodic memory(ies)")
    return 0


def _memory_forget(manager: MemoryManager, args: argparse.Namespace) -> int:
    """Delete one memory."""
    if not asyncio.run(manager.forget(args.memory_id)):
        print(f"myagent: no memory with id {args.memory_id}", file=sys.stderr)
        return 1
    print(f"forgot {args.memory_id}")
    return 0


def _memory_line(record: MemoryRecord) -> str:
    """One-line rendering used by ``memory list`` and ``memory search``."""
    stamp = record.created_at.strftime("%Y-%m-%d %H:%M")
    return (
        f"{record.id}  {record.kind:<8} importance={record.importance:.2f} {stamp}  {record.text}"
    )


def _list_tools() -> int:
    """Print the registered tools; needs no credentials, so it works offline."""
    tools = build_agent().tools
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

    loop = build_agent()
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
