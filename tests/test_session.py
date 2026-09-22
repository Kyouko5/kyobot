"""JSONL session storage: append-only records, replay and archive boundaries."""

from __future__ import annotations

import json

from myagent.agent.types import Message, ToolCallRequest
from myagent.config.settings import AgentSettings
from myagent.session.manager import DEFAULT_SESSION_KEY, JsonlSessionStore, Session


def manager(tmp_path) -> JsonlSessionStore:
    return JsonlSessionStore(tmp_path / "sessions")


def test_a_new_session_starts_empty(tmp_path):
    session = manager(tmp_path).get_or_create(DEFAULT_SESSION_KEY)

    assert session.key == DEFAULT_SESSION_KEY
    assert session.messages == []
    assert session.last_archived == 0
    assert session.created_at


def test_sessions_are_cached_by_key(tmp_path):
    sessions = manager(tmp_path)

    assert sessions.get_or_create("cli:a") is sessions.get_or_create("cli:a")
    assert sessions.get_or_create("cli:a") is not sessions.get_or_create("cli:b")


def test_appended_messages_are_written_as_jsonl_and_reload(tmp_path):
    sessions = manager(tmp_path)
    sessions.append(
        "cli:test",
        [Message.user("hi"), Message.assistant("hello")],
    )
    path = sessions.path_for("cli:test")

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert lines[0] == {
        "type": "session",
        "key": "cli:test",
        "created_at": sessions.get_or_create("cli:test").created_at,
        "last_archived": 0,
        "summary": "",
    }
    assert lines[1] == {"type": "message", "message": {"role": "user", "content": "hi"}}

    reloaded = manager(tmp_path).get_or_create("cli:test")

    assert reloaded.messages == [Message.user("hi"), Message.assistant("hello")]
    assert reloaded.created_at == lines[0]["created_at"]


def test_tool_calls_survive_a_round_trip(tmp_path):
    sessions = manager(tmp_path)
    assistant = Message.assistant(
        None, tool_calls=[ToolCallRequest("call_1", "calculator", {"expression": "1+1"})]
    )
    sessions.append("cli:test", [assistant, Message.tool("call_1", "1+1 = 2")])

    reloaded = manager(tmp_path).get_or_create("cli:test")

    assert reloaded.messages[0].tool_calls[0].arguments == {"expression": "1+1"}
    assert reloaded.messages[1].content == "1+1 = 2"


def test_a_second_append_does_not_rewrite_the_header(tmp_path):
    sessions = manager(tmp_path)
    sessions.append("cli:test", [Message.user("one")])
    sessions.append("cli:test", [Message.user("two")])

    text = sessions.path_for("cli:test").read_text(encoding="utf-8")

    assert text.count('"type": "session"') == 1
    assert manager(tmp_path).get_or_create("cli:test").messages == [
        Message.user("one"),
        Message.user("two"),
    ]


def test_appending_nothing_is_a_no_op(tmp_path):
    sessions = manager(tmp_path)

    sessions.append("cli:test", [])

    assert not sessions.path_for("cli:test").exists()


def test_transcript_respects_the_archive_boundary():
    session = Session(
        key="cli:test",
        messages=[Message.user("old"), Message.user("new")],
        last_archived=1,
    )

    assert session.transcript() == [Message.user("new")]


def test_an_archived_session_is_loaded_with_its_boundary(tmp_path):
    sessions = manager(tmp_path)
    sessions.append("cli:test", [Message.user("one"), Message.user("two")])
    path = sessions.path_for("cli:test")
    document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    document["last_archived"] = 1
    rest = path.read_text(encoding="utf-8").splitlines()[1:]
    path.write_text("\n".join([json.dumps(document), *rest]) + "\n", encoding="utf-8")

    reloaded = manager(tmp_path).get_or_create("cli:test")

    assert reloaded.last_archived == 1
    assert reloaded.transcript() == [Message.user("two")]


def test_unreadable_lines_are_skipped(tmp_path):
    sessions = manager(tmp_path)
    sessions.append("cli:test", [Message.user("good")])
    path = sessions.path_for("cli:test")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write("{ not json\n")
    reloaded = manager(tmp_path).get_or_create("cli:test")

    assert reloaded.messages == [Message.user("good")]


def test_a_corrupt_header_falls_back_to_defaults(tmp_path):
    sessions = manager(tmp_path)
    path = sessions.path_for("cli:test")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json\n", encoding="utf-8")

    session = sessions.get_or_create("cli:test")

    assert session.key == "cli:test"
    assert session.last_archived == 0
    assert session.created_at == ""


def test_a_blank_line_before_the_header_is_skipped(tmp_path):
    sessions = manager(tmp_path)
    sessions.append("cli:test", [Message.user("hi")])
    path = sessions.path_for("cli:test")
    path.write_text("\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    reloaded = manager(tmp_path).get_or_create("cli:test")

    assert [message.content for message in reloaded.messages] == ["hi"]


def test_an_empty_file_has_no_header(tmp_path):
    sessions = manager(tmp_path)
    path = sessions.path_for("cli:test")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")

    assert sessions.get_or_create("cli:test").messages == []


def test_keys_are_percent_encoded_so_every_key_gets_its_own_file(tmp_path):
    sessions = manager(tmp_path)

    assert sessions.path_for("cli:default").name == "cli%3Adefault.jsonl"
    assert sessions.path_for("a/b").name != sessions.path_for("a-b").name


def test_clear_forgets_the_session_and_its_file(tmp_path):
    sessions = manager(tmp_path)
    sessions.append("cli:test", [Message.user("hi")])

    sessions.clear("cli:test")
    sessions.clear("cli:test")

    assert not sessions.path_for("cli:test").exists()
    assert sessions.get_or_create("cli:test").messages == []


def test_known_keys_lists_stored_sessions(tmp_path):
    sessions = manager(tmp_path)
    sessions.append("cli:b", [Message.user("b")])
    sessions.append("cli:a", [Message.user("a")])

    assert sessions.known_keys() == ["cli:a", "cli:b"]
    assert manager(tmp_path / "absent").known_keys() == []


def test_manager_reads_the_sessions_directory_from_settings(tmp_path):
    settings = AgentSettings(sessions_dir=tmp_path / "store")

    sessions = JsonlSessionStore.from_settings(settings)

    assert sessions.sessions_dir == tmp_path / "store"
