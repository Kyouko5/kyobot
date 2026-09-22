"""Environment and secret loading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from myagent.config.env import (
    ENV_FILE_VAR,
    MissingEnvError,
    discover_env_file,
    get_bool_env,
    get_env,
    load_env,
    remember_env,
    require_env,
)


@pytest.fixture
def isolated_environ(monkeypatch):
    """Give each test a private copy of ``os.environ`` that is restored afterwards."""
    copy = dict(os.environ)
    monkeypatch.setattr(os, "environ", copy)
    return copy


@pytest.fixture
def no_env_override(monkeypatch):
    """Let discovery run normally (conftest pins ``MYAGENT_ENV_FILE`` for isolation)."""
    monkeypatch.delenv(ENV_FILE_VAR, raising=False)


def write_env(path: Path, **values: str) -> Path:
    env_file = path / ".env"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8"
    )
    return env_file


def test_load_env_reads_a_file_through_load_dotenv(tmp_path, isolated_environ):
    env_file = write_env(tmp_path, LLM_API_KEY="sk-test")

    loaded = load_env(env_file)

    assert loaded == env_file
    assert isolated_environ["LLM_API_KEY"] == "sk-test"


def test_load_env_returns_none_when_no_file_is_found(
    tmp_path, isolated_environ, monkeypatch, no_env_override
):
    monkeypatch.chdir(tmp_path)

    assert discover_env_file() is None
    assert load_env() is None


def test_load_env_raises_for_an_explicitly_requested_missing_file(tmp_path, isolated_environ):
    with pytest.raises(FileNotFoundError, match="env file not found"):
        load_env(tmp_path / "missing.env")


def test_process_environment_wins_over_the_file(tmp_path, isolated_environ):
    env_file = write_env(tmp_path, LLM_MODEL="from-file")
    isolated_environ["LLM_MODEL"] = "from-shell"

    load_env(env_file)

    assert isolated_environ["LLM_MODEL"] == "from-shell"


def test_override_replaces_existing_values(tmp_path, isolated_environ):
    env_file = write_env(tmp_path, LLM_MODEL="from-file")
    isolated_environ["LLM_MODEL"] = "from-shell"

    load_env(env_file, override=True)

    assert isolated_environ["LLM_MODEL"] == "from-file"


def test_env_file_variable_points_at_an_explicit_file(tmp_path, isolated_environ, monkeypatch):
    env_file = write_env(tmp_path, MYAGENT_LOG_LEVEL="DEBUG")
    monkeypatch.chdir(tmp_path.parent)
    isolated_environ[ENV_FILE_VAR] = str(env_file)

    assert discover_env_file() == env_file
    assert load_env() == env_file
    assert isolated_environ["MYAGENT_LOG_LEVEL"] == "DEBUG"


def test_env_file_variable_must_point_at_a_real_file(tmp_path, isolated_environ):
    isolated_environ[ENV_FILE_VAR] = str(tmp_path / "nope.env")

    with pytest.raises(FileNotFoundError, match=ENV_FILE_VAR):
        discover_env_file()


def test_discover_env_file_searches_upwards_from_the_working_directory(
    tmp_path, isolated_environ, monkeypatch, no_env_override
):
    write_env(tmp_path, MYAGENT_LOG_FORMAT="json")
    nested = tmp_path / "src" / "myagent"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert discover_env_file() == tmp_path / ".env"


def test_discover_env_file_can_look_in_one_directory_only(
    tmp_path, isolated_environ, no_env_override
):
    assert discover_env_file(start=tmp_path) is None

    env_file = write_env(tmp_path, MYAGENT_LOG_LEVEL="DEBUG")

    assert discover_env_file(start=tmp_path) == env_file


def test_blank_values_count_as_unset(tmp_path, isolated_environ):
    env_file = write_env(tmp_path, LLM_API_KEY="", EMBED_API_KEY="")
    load_env(env_file)

    assert get_env("LLM_API_KEY") is None
    assert get_env("LLM_API_KEY", "fallback") == "fallback"
    with pytest.raises(MissingEnvError):
        require_env("LLM_API_KEY")


def test_require_env_returns_values_and_raises_for_missing_ones(isolated_environ):
    isolated_environ["LLM_API_KEY"] = "  sk-test  "

    assert require_env("LLM_API_KEY") == "sk-test"
    with pytest.raises(MissingEnvError, match="EMBED_API_KEY"):
        require_env("EMBED_API_KEY")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("1", True), ("YES", True), ("off", False), ("false", False)],
)
def test_get_bool_env_understands_common_words(raw, expected, isolated_environ):
    isolated_environ["MYAGENT_QDRANT_PREFER_GRPC"] = raw

    assert get_bool_env("MYAGENT_QDRANT_PREFER_GRPC") is expected


def test_get_bool_env_defaults_and_rejects_junk(isolated_environ):
    assert get_bool_env("MYAGENT_QDRANT_PREFER_GRPC") is False
    assert get_bool_env("MYAGENT_QDRANT_PREFER_GRPC", default=True) is True

    isolated_environ["MYAGENT_QDRANT_PREFER_GRPC"] = "maybe"
    with pytest.raises(ValueError, match="must be a boolean"):
        get_bool_env("MYAGENT_QDRANT_PREFER_GRPC")


# --- Phase 5: writing a value back (the EMBED_DIM probe) ---------------------


def test_remember_env_fills_a_blank_line_in_place(tmp_path, isolated_environ):
    env_file = write_env(tmp_path, LLM_MODEL="m", EMBED_DIM="", QDRANT_URL="http://localhost:6333")
    isolated_environ[ENV_FILE_VAR] = str(env_file)

    updated = remember_env("EMBED_DIM", "1024")

    assert updated == env_file
    assert os.environ["EMBED_DIM"] == "1024"
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "LLM_MODEL=m",
        "EMBED_DIM=1024",
        "QDRANT_URL=http://localhost:6333",
    ]


def test_remember_env_appends_a_missing_variable(tmp_path, isolated_environ):
    env_file = write_env(tmp_path, LLM_MODEL="m")
    isolated_environ[ENV_FILE_VAR] = str(env_file)

    remember_env("EMBED_DIM", "1024")

    assert env_file.read_text(encoding="utf-8").splitlines() == ["LLM_MODEL=m", "EMBED_DIM=1024"]
    load_env()  # a later run reads exactly what was written
    assert get_env("EMBED_DIM") == "1024"


def test_remember_env_leaves_comments_and_other_variables_alone(tmp_path, isolated_environ):
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\nEMBED_DIM=\n", encoding="utf-8")
    isolated_environ[ENV_FILE_VAR] = str(env_file)

    remember_env("EMBED_DIM", "768")

    assert env_file.read_text(encoding="utf-8") == "# a comment\nEMBED_DIM=768\n"


def test_remember_env_updates_the_process_when_there_is_no_file(
    tmp_path, isolated_environ, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_FILE_VAR, raising=False)

    assert remember_env("EMBED_DIM", "1536") is None
    assert os.environ["EMBED_DIM"] == "1536"
