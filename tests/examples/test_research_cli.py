"""Research CLI dispatch, confinement and session behaviour without network calls."""

from __future__ import annotations

import argparse
import builtins
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from myagent import cli
from myagent.agent.loop import TurnContext
from myagent.agent.types import OutboundMessage, StopReason
from myagent.config.env import MissingEnvError
from myagent.config.settings import LLMSettings
from myagent.rag.embedder import EmbeddingError
from myagent.rag.loader import UnreadableDocumentError
from myagent.rag.vectorstore import VectorStoreError
from myagent.research import cli as research_cli
from myagent.research.agent import ResearchSettings


class FakeLoop:
    def __init__(self, *, error: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.cleared: list[str] = []
        self.sessions = SimpleNamespace(clear=self.cleared.append)
        self.error = error

    async def run_turn(self, question: str, session_key: str) -> TurnContext:
        self.calls.append((question, session_key))
        turn = TurnContext(session_key, question)
        turn.outbound = OutboundMessage(
            session_key,
            "offline answer",
            StopReason.ERROR if self.error else StopReason.COMPLETED,
        )
        return turn


@pytest.fixture
def patched(corpus, monkeypatch):
    monkeypatch.setattr(research_cli.Settings, "from_env", classmethod(lambda cls: corpus.settings))
    monkeypatch.setattr(
        research_cli.ResearchSettings,
        "from_env",
        classmethod(lambda cls: ResearchSettings(corpus.root)),
    )
    monkeypatch.setattr(research_cli, "build_rag", lambda settings: corpus.rag)
    loop = FakeLoop()
    monkeypatch.setattr(research_cli, "build_research_agent", lambda *args, **kwargs: loop)
    return loop


def test_research_ingest_accepts_only_pdfs_inside_the_root(corpus, patched, capsys, tmp_path):
    path = corpus.root / "graph_indexing.pdf"
    assert cli.main(["research", "ingest", str(path)]) == 0
    assert corpus.ids[0] in capsys.readouterr().out
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(path.read_bytes())
    assert cli.main(["research", "ingest", str(outside)]) == 2
    assert "must be a PDF inside" in capsys.readouterr().err
    wrong_type = corpus.root / "notes.txt"
    wrong_type.write_text("notes")
    assert cli.main(["research", "ingest", str(wrong_type)]) == 2


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (MissingEnvError("EMBED_API_KEY"), 2),
        (UnreadableDocumentError("missing paper"), 1),
        (EmbeddingError("embedding down"), 1),
        (VectorStoreError("qdrant down"), 1),
    ],
)
def test_research_ingest_reports_dependency_failures(
    corpus, patched, monkeypatch, capsys, failure, code
):
    class Broken:
        async def ingest(self, paths: list[Path]):
            raise failure

    monkeypatch.setattr(research_cli, "build_rag", lambda settings: Broken())
    path = corpus.root / "graph_indexing.pdf"

    assert cli.main(["research", "ingest", str(path)]) == code
    assert str(failure) in capsys.readouterr().err


def test_research_ask_checks_scope_and_namespaces_sessions(corpus, patched, capsys):
    graph_id = corpus.ids[0]
    assert (
        cli.main(["research", "ask", "What is indexed?", "--paper", graph_id, "--session", "work"])
        == 0
    )
    assert patched.calls == [("What is indexed?", "research:work")]
    assert "offline answer" in capsys.readouterr().out
    assert cli.main(["research", "ask", "question", "--paper", "unknown"]) == 1
    assert "no accessible paper" in capsys.readouterr().err


def test_research_ask_reports_model_failure(corpus, patched, monkeypatch, capsys):
    broken = FakeLoop(error=True)
    monkeypatch.setattr(research_cli, "build_research_agent", lambda *args, **kwargs: broken)

    assert cli.main(["research", "ask", "question"]) == 1
    assert "offline answer" in capsys.readouterr().out


def test_research_refuses_bad_session_names_and_missing_llm(corpus, patched, monkeypatch, capsys):
    assert cli.main(["research", "ask", "x", "--session", "../escape"]) == 2
    assert "--session" in capsys.readouterr().err
    missing_model = replace(corpus.settings, llm=LLMSettings(api_key="key"))
    monkeypatch.setattr(research_cli.Settings, "from_env", classmethod(lambda cls: missing_model))
    assert cli.main(["research", "ask", "x"]) == 2
    assert "LLM_MODEL" in capsys.readouterr().err


def test_research_chat_supports_session_clear_empty_and_exit(patched, monkeypatch, capsys):
    lines = iter(["", "/session", "What is indexed?", "/clear", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda prompt: next(lines))

    assert cli.main(["research", "chat", "--session", "work"]) == 0
    assert patched.calls == [("What is indexed?", "research:work")]
    assert patched.cleared == ["research:work"]
    assert "session: research:work" in capsys.readouterr().out


def test_research_chat_accepts_eof(patched, monkeypatch):
    def eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)

    assert cli.main(["research", "chat"]) == 0


def test_research_parser_rejects_missing_subcommand():
    parser = argparse.ArgumentParser()
    verbs = parser.add_subparsers(dest="command")
    research_cli.add_research_parser(verbs)

    with pytest.raises(SystemExit):
        parser.parse_args(["research"])
