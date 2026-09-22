"""Command line entry point.

    myagent chat -m "现在几点"    # one message
    myagent chat                  # interactive (/exit, /session, /clear)
    myagent tools                 # list the registered tools
    myagent memory list --kind semantic -n 20
    myagent memory search "我的研究方向" -k 5
    myagent memory add "用户偏好 Python" --kind semantic --importance 0.8
    myagent memory consolidate --dry-run
    myagent memory forget <memory_id>
    myagent ingest data/papers/*.pdf
    myagent search "GraphRAG 的核心思想" -k 5
    myagent docs list
    myagent docs delete <document_id>
    myagent session compact cli:default   # 把旧对话压成摘要检查点（Phase 6）
    myagent chat --show-context           # 打印这一轮各 section 的预算账本（Phase 6）
    myagent web --port 8080               # 在浏览器里用同一个 agent（Phase G）

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
from pathlib import Path

from myagent.agent.compaction import DEFAULT_KEEP_RECENT_TURNS, ModelSummarizer, compact_session
from myagent.agent.context import ContextBundle, ContextReport, SectionReport
from myagent.agent.loop import AgentLoop, TurnContext
from myagent.config.env import MissingEnvError
from myagent.config.settings import DEFAULT_RAG_TOP_K, LLMSettings, Settings
from myagent.gateway import serve as serve_gateway
from myagent.gateway.server import DEFAULT_HOST, DEFAULT_PORT
from myagent.memory.manager import MemoryManager
from myagent.memory.types import KINDS, Kind, MemoryRecord
from myagent.models.base import LLMError
from myagent.models.openai_compat import OpenAICompatModel
from myagent.observability.logging import configure_logging
from myagent.rag.embedder import EmbeddingError
from myagent.rag.loader import LoaderError
from myagent.rag.pipeline import RagPipeline, citation
from myagent.rag.types import RetrievedChunk
from myagent.rag.vectorstore import VectorStoreError
from myagent.runtime import build_agent, build_memory, build_rag
from myagent.session.base import DEFAULT_SESSION_KEY
from myagent.session.manager import JsonlSessionStore

__all__ = ["main"]

_PROMPT = "you> "
_ANSWER_PREFIX = "agent> "
_COMMANDS = ("/exit", "/quit", "/session", "/clear")
_SNIPPET_CHARS = 200


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the requested command."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging()

    if args.command == "tools":
        return _list_tools()
    if args.command == "memory":
        return _memory(args)
    if args.command in ("ingest", "search", "docs"):
        return _rag(args)
    if args.command == "session":
        return _session(args)
    if args.command == "web":
        return _web(args)
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

    chat.add_argument(
        "--show-context",
        action="store_true",
        help="print each context section's budget before the answer (Phase 6)",
    )

    subcommands.add_parser("tools", help="list the registered tools")
    _add_web_parser(subcommands)
    _add_memory_parser(subcommands)
    _add_rag_parsers(subcommands)
    _add_session_parser(subcommands)
    return parser


def _add_web_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``myagent web``: the browser UI on a local port (PLAN Phase G).

    The defaults stay on loopback because the gateway has no authentication:
    ``--allow-remote`` is the deliberate, warned-about way to leave it.
    """
    web = subcommands.add_parser("web", help="serve the browser UI on a local port")
    web.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    web.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (0 picks a free one)")
    web.add_argument(
        "--allow-remote",
        action="store_true",
        help="accept requests from other hosts too (the gateway has no authentication)",
    )
    web.add_argument("--no-open", action="store_true", help="do not open a browser window")


def _add_session_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """``myagent session compact <key>``: PLAN 6.4's explicit compaction.

    One verb, because the other session operations already exist where they are
    used (``/clear`` and ``/session`` inside ``myagent chat``, and the raw JSONL
    under ``AGENT_SESSIONS_DIR`` for everything else).
    """
    session = subcommands.add_parser("session", help="inspect and maintain stored sessions")
    verbs = session.add_subparsers(dest="session_command", required=True)

    compact = verbs.add_parser("compact", help="summarise old turns into a checkpoint")
    compact.add_argument("key", help=f"session key to compact (default {DEFAULT_SESSION_KEY})")
    compact.add_argument(
        "--keep-recent",
        type=int,
        default=DEFAULT_KEEP_RECENT_TURNS,
        help=f"how many turns stay verbatim (default {DEFAULT_KEEP_RECENT_TURNS})",
    )


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


def _add_rag_parsers(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``myagent ingest|search|docs``: the RAG commands of PLAN 5.8.

    Two flat verbs (``ingest``, ``search``) and one noun with sub-verbs
    (``docs list`` / ``docs delete``), mirroring ``myagent memory ...``.
    """
    ingest = subcommands.add_parser("ingest", help="ingest documents into the knowledge base")
    ingest.add_argument("paths", nargs="+", help="files to ingest (.pdf / .md / .txt)")

    search = subcommands.add_parser("search", help="search the ingested documents")
    search.add_argument("query", help="what to look for")
    search.add_argument(
        "-k", "--top-k", type=int, default=DEFAULT_RAG_TOP_K, help="how many chunks to show"
    )
    search.add_argument(
        "-d", "--document", action="append", help="restrict to one document id (repeatable)"
    )

    docs = subcommands.add_parser("docs", help="list or delete ingested documents")
    verbs = docs.add_subparsers(dest="docs_command", required=True)
    verbs.add_parser("list", help="show the ingested documents")
    delete = verbs.add_parser("delete", help="forget one document and its vectors")
    delete.add_argument("document_id", help="the id shown by 'docs list'")


def _rag(args: argparse.Namespace) -> int:
    """Dispatch the RAG commands; none of them need a chat model."""
    pipeline = build_rag(Settings.from_env())
    if args.command == "ingest":
        return _rag_ingest(pipeline, args)
    if args.command == "search":
        return _rag_search(pipeline, args)
    if args.docs_command == "list":
        return _rag_docs_list(pipeline)
    return _rag_docs_delete(pipeline, args)


def _rag_ingest(pipeline: RagPipeline, args: argparse.Namespace) -> int:
    """Load, chunk, embed and index every path; report one line per document."""
    try:
        report = asyncio.run(pipeline.ingest([Path(path) for path in args.paths]))
    except MissingEnvError as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 2
    except (LoaderError, EmbeddingError, VectorStoreError) as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 1
    for document in report.documents:
        action = "added" if document.created else "updated"
        title = f"  {document.title}" if document.title else ""
        print(f"{action} {document.document_id}  {document.chunks} chunk(s){title}")
        print(f"    {document.source}")
    if report.dim is not None:
        probed = " (probed, written to .env)" if report.dim_probed else ""
        print(f"embedding dim={report.dim}{probed}")
    print(f"{report.added} added, {report.updated} updated, {report.chunk_count} chunk(s) total")
    return 0


def _rag_search(pipeline: RagPipeline, args: argparse.Namespace) -> int:
    """Search the knowledge base, printing citations the user can follow up on."""
    try:
        hits = asyncio.run(
            pipeline.retrieve(args.query, args.top_k, document_ids=args.document or None)
        )
    except MissingEnvError as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 2
    except (EmbeddingError, VectorStoreError) as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 1
    if not hits:
        print(f"no document chunk matched {args.query!r}")
        return 0
    for hit in hits:
        print(_hit_line(hit))
    return 0


def _rag_docs_list(pipeline: RagPipeline) -> int:
    """List the knowledge base; reads SQLite only, so it works offline."""
    documents = pipeline.documents()
    if not documents:
        print("no documents yet; ingest one with `myagent ingest <file>`")
        return 0
    for document in documents:
        stamp = document.created_at.strftime("%Y-%m-%d %H:%M")
        print(
            f"{document.id}  {document.chunks:>4} chunk(s)  {document.pages:>3} page(s)  "
            f"{stamp}  {document.source}"
        )
    print(f"({len(documents)} document(s), {sum(d.chunks for d in documents)} chunk(s))")
    return 0


def _rag_docs_delete(pipeline: RagPipeline, args: argparse.Namespace) -> int:
    """Delete one document: its chunk rows and its vectors."""
    try:
        deleted = pipeline.delete(args.document_id)
    except VectorStoreError as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 1
    if not deleted:
        print(f"myagent: no document with id {args.document_id}", file=sys.stderr)
        return 1
    print(f"deleted {args.document_id} and its chunk vectors")
    return 0


def _hit_line(hit: RetrievedChunk) -> str:
    """One retrieved chunk: its ``[document#index]`` citation, score and snippet."""
    snippet = " ".join(hit.chunk.text.split())
    if len(snippet) > _SNIPPET_CHARS:
        snippet = snippet[:_SNIPPET_CHARS] + "…"
    return f"{citation(hit)}  score={hit.score:.3f}\n    {snippet}"


def _session(args: argparse.Namespace) -> int:
    """Dispatch ``myagent session <verb>``; needs SQLite/session files, not a model."""
    sessions = JsonlSessionStore.from_settings(Settings.from_env().agent)
    return _session_compact(sessions, args)


def _session_compact(sessions: JsonlSessionStore, args: argparse.Namespace) -> int:
    """Sumarise the turns before the last ``--keep-recent`` into a checkpoint.

    The summary is the one thing here that needs a model, so the credentials are
    checked before anything is read: a failure must not leave a half-compacted
    session behind. The write itself is a single
    :meth:`~myagent.session.base.SessionStore.commit_summary`, after which the
    replayed transcript starts later and the JSONL keeps every original line.
    """
    llm_settings = LLMSettings.from_env()
    try:
        llm_settings.require_model()
        llm_settings.require_api_key()
    except MissingEnvError as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 2
    session = sessions.get_or_create(args.key)
    if not session.messages:
        print(f"session {args.key} has no messages yet")
        return 0
    summarizer = ModelSummarizer(OpenAICompatModel(llm_settings))
    try:
        result = asyncio.run(
            compact_session(session, summarizer, keep_recent_turns=args.keep_recent)
        )
    except LLMError as exc:
        print(f"myagent: could not summarise {args.key}: {exc}", file=sys.stderr)
        return 1
    report = result.report
    if not report.compacted:
        print(
            f"nothing to compact: {args.key} has fewer than {args.keep_recent + 1} "
            "turn(s) to replay"
        )
        return 0
    sessions.commit_summary(args.key, summary=result.summary, boundary=result.boundary)
    print(
        f"compacted {args.key}: {report.turns_removed} turn(s) "
        f"({report.messages_removed} message(s)) -> summary"
    )
    print(
        f"  tokens: {report.before_tokens} -> {report.after_tokens} (saved {report.tokens_saved})"
    )
    print(f"  replay starts at message {report.boundary}; originals stay in the JSONL file")
    print(f"summary:\n{result.summary}")
    return 0


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


def _web(args: argparse.Namespace) -> int:
    """Serve the browser UI and block until ``Ctrl+C`` (PLAN Phase G).

    Deliberately does not require credentials: starting the gateway *without* a
    key is how the settings page gets its first one. A turn started before that
    answers with the same ``MissingEnvError`` text the chat endpoint reports.
    """
    return serve_gateway(
        host=args.host,
        port=args.port,
        allow_remote=args.allow_remote,
        open_browser=not args.no_open,
    )


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
        turn = asyncio.run(loop.run_turn(args.message, args.session))
        if args.show_context:
            print(_context_transcript(turn))
        print(turn.require_outbound().content)
        return 0
    return asyncio.run(_interactive(loop, args.session, show_context=args.show_context))


def _context_transcript(ctx: TurnContext) -> str:
    """The ``--show-context`` view: every section with its budget accounting.

    PLAN 6's acceptance test reads this transcript, so it prints the four
    sections by name (``conversation`` / ``memory`` / ``rag`` / ``tools``) plus
    the two required ones, each with the budget it was given, the tokens it used
    and what the trimmer did to it (``ContextReport``).
    """
    bundle = ctx.require_bundle()
    report = bundle.report if bundle.report is not None else _unbudgeted_report(bundle)
    limit = "unlimited" if report.input_tokens is None else str(report.input_tokens)
    lines = [
        f"--- context for {ctx.session_key} (turn {ctx.turn_id})",
        f"budget {limit} token(s), used {report.used}, dropped {report.dropped}",
    ]
    for entry in report.sections:
        kind = "required" if entry.required else "optional"
        quota = "-" if entry.budget is None else str(entry.budget)
        action = f"  {entry.action}" if entry.action else ""
        lines.append(
            f"  {entry.name:<12} priority={entry.priority} {kind:<8} "
            f"used={entry.used:<7} budget={quota}{action}"
        )
    lines.append(f"  messages ({len(bundle.messages)}): {', '.join(_roles(bundle))}")
    if bundle.compaction is not None and bundle.compaction.compacted:
        compacted = bundle.compaction
        lines.append(
            f"  conversation compacted: -{compacted.messages_removed} message(s), "
            f"-{compacted.tokens_saved} token(s)"
        )
    lines.append("---")
    return "\n".join(lines)


def _unbudgeted_report(bundle: ContextBundle) -> ContextReport:
    """A report for a context manager that does not produce one (all quotas unknown)."""
    return ContextReport(
        sections=tuple(
            SectionReport(
                name=section.name,
                priority=section.priority,
                required=section.required,
                budget=section.budget_tokens,
                used=section.estimated_tokens(),
            )
            for section in bundle.sections
        ),
        used=bundle.estimated_tokens,
    )


def _roles(bundle: ContextBundle) -> list[str]:
    """The message roles, with the repeated ones collapsed (``user x3``)."""
    roles: list[str] = []
    for message in bundle.messages:
        if roles and roles[-1].startswith(f"{message.role} x"):
            times = int(roles[-1].split(" x")[1]) + 1
            roles[-1] = f"{message.role} x{times}"
        elif roles and roles[-1] == message.role:
            roles[-1] = f"{message.role} x2"
        else:
            roles.append(message.role)
    return roles


async def _interactive(loop: AgentLoop, session_key: str, *, show_context: bool = False) -> int:
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
        turn = await loop.run_turn(text, session_key)
        if show_context:
            print(_context_transcript(turn))
        print(f"{_ANSWER_PREFIX}{turn.require_outbound().content}")
