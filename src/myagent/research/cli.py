"""``myagent research`` commands: ingest, scoped ask and persistent chat."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from myagent.agent.loop import AgentLoop
from myagent.agent.types import StopReason
from myagent.config.env import MissingEnvError
from myagent.config.settings import Settings
from myagent.rag.embedder import EmbeddingError
from myagent.rag.loader import LoaderError
from myagent.rag.vectorstore import VectorStoreError
from myagent.research.agent import ResearchSettings, build_research_agent
from myagent.research.tools import PaperLibrary
from myagent.runtime import build_rag

__all__ = ["add_research_parser", "run_research"]

_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def add_research_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Install the application commands without changing the generic runtime."""
    research = subcommands.add_parser("research", help="read and discuss local research papers")
    verbs = research.add_subparsers(dest="research_command", required=True)
    ingest = verbs.add_parser("ingest", help="ingest PDFs from MYAGENT_PAPERS_DIR")
    ingest.add_argument("paths", nargs="+", help="PDF paths under data/papers")
    chat = verbs.add_parser("chat", help="start a persistent research conversation")
    chat.add_argument("--session", default="default", help="research session name")
    ask = verbs.add_parser("ask", help="ask one research question")
    ask.add_argument("question", help="the question to answer")
    ask.add_argument("--paper", help="restrict retrieval and citations to one document ID")
    ask.add_argument("--session", default="default", help="research session name")


def run_research(args: argparse.Namespace) -> int:
    """Dispatch one application command with the same `.env` as generic MyAgent."""
    settings = Settings.from_env()
    research = ResearchSettings.from_env()
    if args.research_command == "ingest":
        return _ingest(settings, research, args.paths)
    if not _SESSION_PATTERN.fullmatch(args.session):
        print(
            "myagent: --session must be 1..64 letters, digits, dots, dashes or underscores",
            file=sys.stderr,
        )
        return 2
    try:
        settings.llm.require_model()
        settings.llm.require_api_key()
    except MissingEnvError as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 2
    rag = build_rag(settings)
    scoped = (args.paper,) if args.research_command == "ask" and args.paper else None
    if scoped is not None and PaperLibrary(rag, research.papers_dir).get(scoped[0]) is None:
        print(f"myagent: no accessible paper with id {scoped[0]}", file=sys.stderr)
        return 1
    loop = build_research_agent(settings, research=research, document_ids=scoped, rag=rag)
    key = f"research:{args.session}"
    if args.research_command == "ask":
        return asyncio.run(_ask(loop, args.question, key))
    return asyncio.run(_chat(loop, key))


def _ingest(settings: Settings, research: ResearchSettings, paths: Sequence[str]) -> int:
    """Ingest only PDFs physically inside the configured corpus root."""
    root = research.papers_dir.expanduser().resolve()
    resolved: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".pdf":
            print(f"myagent: {raw!r} must be a PDF inside {root}", file=sys.stderr)
            return 2
        resolved.append(path)
    try:
        report = asyncio.run(build_rag(settings).ingest(resolved))
    except MissingEnvError as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 2
    except (LoaderError, EmbeddingError, VectorStoreError) as exc:
        print(f"myagent: {exc}", file=sys.stderr)
        return 1
    for document in report.documents:
        print(f"{document.document_id}  {document.chunks} chunk(s)  {document.source}")
    print(f"{report.added} added, {report.updated} updated")
    return 0


async def _ask(loop: AgentLoop, question: str, key: str) -> int:
    turn = await loop.run_turn(question, key)
    answer = turn.require_outbound()
    print(answer.content)
    return 1 if answer.stop_reason is StopReason.ERROR else 0


async def _chat(loop: AgentLoop, key: str) -> int:
    print(f"session: {key} (commands: /exit, /session, /clear)")
    while True:
        try:
            line = await asyncio.to_thread(input, "research> ")
        except EOFError:
            print()
            return 0
        question = line.strip()
        if not question:
            continue
        if question in ("/exit", "/quit"):
            return 0
        if question == "/session":
            print(f"session: {key}")
            continue
        if question == "/clear":
            loop.sessions.clear(key)
            print("cleared")
            continue
        await _ask(loop, question, key)
