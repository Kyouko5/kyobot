"""Tool contract, registry gateway and the four built-in tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from myagent.config.settings import AgentSettings
from myagent.tools.base import Tool, ToolResult, resolve_type, validate_schema_value
from myagent.tools.builtin import (
    CalculatorTool,
    CurrentTimeTool,
    ReadFileTool,
    SearchLocalTool,
    build_default_registry,
)
from myagent.tools.builtin.paths import WorkspaceError, resolve_in_workspace
from myagent.tools.registry import RETRY_HINT, ToolRegistry


class DemoTool(Tool):
    """A tool whose schema exercises casting and validation rules."""

    name = "demo"
    description = "demo tool"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
            "ratio": {"type": "number"},
            "label": {"type": "string", "minLength": 2, "maxLength": 4},
            "flag": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
            "note": {"type": ["string", "null"]},
            "nested": {
                "type": "object",
                "properties": {"inner": {"type": "integer"}},
                "required": ["inner"],
            },
            "items": {"type": "array", "items": {"type": "integer"}},
            "metadata": {"type": "object", "additionalProperties": {"type": "integer"}},
        },
        "required": ["count"],
        "additionalProperties": False,
    }

    def __init__(self, *, read_only: bool = True, exclusive: bool = False) -> None:
        self.read_only = read_only
        self.exclusive = exclusive
        self.calls: list[dict[str, Any]] = []

    @property
    def parameters(self) -> dict[str, Any]:
        return {**self.parameters_schema, "properties": dict(self.parameters_schema["properties"])}

    async def execute(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult("demo ok")


class ExplodingTool(Tool):
    """A tool that raises, to prove the registry contains the damage."""

    name = "exploding"
    description = "always fails"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> ToolResult:
        raise RuntimeError("kaboom")


def registry() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(DemoTool())
    tools.register(ExplodingTool())
    return tools


def test_tool_result_is_a_string_with_an_error_flag():
    ok = ToolResult("fine")
    bad = ToolResult.error("broken")

    assert isinstance(ok, str)
    assert ok == "fine"
    assert ok.is_error is False
    assert bad == "broken"
    assert bad.is_error is True


def test_tool_schema_and_concurrency_flags():
    tool = DemoTool()
    alone = DemoTool(read_only=False)
    exclusive = DemoTool(exclusive=True)

    assert tool.to_schema()["function"]["name"] == "demo"
    assert tool.concurrency_safe is True
    assert alone.concurrency_safe is False
    assert exclusive.concurrency_safe is False


def test_registry_exposes_sorted_definitions_and_basics():
    tools = registry()
    tools.register(CalculatorTool())

    assert len(tools) == 3
    assert "demo" in tools
    assert "missing" not in tools
    assert 42 not in tools
    assert tools.has("demo") is True
    assert tools.get("missing") is None
    assert [entry["function"]["name"] for entry in tools.get_definitions()] == [
        "calculator",
        "demo",
        "exploding",
    ]

    tools.unregister("demo")
    tools.unregister("demo")

    assert tools.has("demo") is False
    assert len(tools) == 2


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"count": "3"}, 3),
        ({"count": 3}, 3),
    ],
)
def test_integer_coercion_uses_the_schema(params, expected):
    _, cast_params, error = registry().prepare_call("demo", params)

    assert error is None
    assert cast_params["count"] == expected


def test_a_failed_cast_is_reported_by_validation():
    _, cast_params, error = registry().prepare_call("demo", {"count": "not a number"})

    assert cast_params["count"] == "not a number"
    assert error is not None and "count should be integer" in error


def test_coercion_covers_numbers_booleans_strings_arrays_and_objects():
    _, params, error = registry().prepare_call(
        "demo",
        {
            "count": 2,
            "ratio": "0.5",
            "label": 12,
            "flag": "yes",
            "items": ["1", "2"],
            "nested": {"inner": "7"},
            "metadata": {"pages": "12"},
        },
    )

    assert error is None
    assert params["ratio"] == 0.5
    assert params["label"] == "12"
    assert params["flag"] is True
    assert params["items"] == [1, 2]
    assert params["nested"] == {"inner": 7}
    assert params["metadata"] == {"pages": 12}


def test_boolean_coercion_keeps_unknown_words():
    _, params, _ = registry().prepare_call("demo", {"count": 1, "flag": "maybe"})
    _, falsy, _ = registry().prepare_call("demo", {"count": 1, "flag": "no"})

    assert params["flag"] == "maybe"
    assert falsy["flag"] is False


def test_params_may_arrive_as_a_json_string():
    _, params, error = registry().prepare_call("demo", '{"count": 4}')

    assert error is None
    assert params["count"] == 4


def test_broken_json_params_are_reported_as_not_an_object():
    tool, params, error = registry().prepare_call("demo", "{not json")

    assert tool is not None
    assert params == {}
    assert error is not None and "must be a JSON object, got str" in error


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, "missing required count"),
        ({"count": 99}, "count must be <= 10"),
        ({"count": 0}, "count must be >= 1"),
        ({"count": 1, "label": "x"}, "label must be at least 2 chars"),
        ({"count": 1, "label": "toolong"}, "label must be at most 4 chars"),
        ({"count": 1, "mode": "turbo"}, "mode must be one of ['fast', 'slow']"),
        ({"count": 1, "extra": 1}, "unexpected parameter extra"),
        ({"count": 1, "nested": {}}, "missing required nested.inner"),
        ({"count": 1, "nested": {"inner": "x"}}, "nested.inner should be integer"),
        ({"count": 1, "items": ["x"]}, "[0] should be integer"),
        ({"count": 1, "ratio": "x"}, "ratio should be number"),
    ],
)
def test_schema_validation_messages(params, expected):
    _, _, error = registry().prepare_call("demo", params)

    assert error is not None
    assert expected in error
    assert error.startswith("Error: Invalid parameters for tool 'demo':")


def test_nullable_and_unknown_schema_types_are_tolerated():
    _, params, error = registry().prepare_call("demo", {"count": 1, "note": None})

    assert error is None and params["note"] is None
    assert validate_schema_value("anything", {"type": "mystery"}) == []
    assert validate_schema_value(None, {"type": "string"}) == ["parameter should be string"]
    assert validate_schema_value(None, {"type": "string", "nullable": True}) == []
    assert validate_schema_value([1], {"type": "array"}) == []
    assert validate_schema_value({"a": 1}, {"type": "object"}) == []
    assert resolve_type(["integer", "null"]) == "integer"
    assert resolve_type(None) is None


def test_schema_type_that_is_not_an_object_is_a_programming_error():
    class BrokenTool(DemoTool):
        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "string"}

    broken = BrokenTool()
    with pytest.raises(ValueError, match="tool schema must be an object type"):
        broken.validate_params({"count": 1})
    assert broken.cast_params({"anything": 1}) == {"anything": 1}


def test_non_object_parameters_are_rejected_by_validation():
    tool = DemoTool()

    assert tool.validate_params("nope") == ["parameters must be an object, got str"]
    assert tool.cast_params({"count": 1})["count"] == 1


def test_a_plain_string_argument_is_not_decoded():
    _, params, error = registry().prepare_call("demo", "5")

    assert params == {}
    assert error is not None and "must be a JSON object, got str" in error


def test_a_name_without_letters_cannot_be_suggested():
    _, _, error = registry().prepare_call("", {})

    assert error is not None
    assert "Did you mean" not in error


def test_unknown_tool_names_get_a_suggestion_when_unambiguous():
    _, _, suggested = registry().prepare_call("DEMO", {})
    _, _, unsuggested = registry().prepare_call("nope", {})
    _, _, no_match = registry().prepare_call("calculator", {})

    assert suggested is not None and "Did you mean 'demo'?" in suggested
    assert unsuggested is not None and "Did you mean" not in unsuggested
    assert "Available: demo, exploding" in unsuggested
    assert no_match is not None and "not found" in no_match


async def test_execute_returns_the_tool_output():
    result = await registry().execute("demo", {"count": 1})

    assert result == "demo ok"
    assert result.is_error is False


async def test_execute_turns_preparation_errors_into_results():
    result = await registry().execute("demo", {})

    assert result.is_error is True
    assert result.endswith(RETRY_HINT)
    assert "missing required count" in result


async def test_execute_contains_tool_exceptions():
    result = await registry().execute("exploding", {})

    assert result.is_error is True
    assert "Error executing exploding: kaboom" in result
    assert result.endswith(RETRY_HINT)


async def test_execute_keeps_an_error_result_and_marks_it():
    class FailingTool(DemoTool):
        name = "failing"

        async def execute(self, **kwargs: Any) -> ToolResult:
            return ToolResult.error("no thanks")

    tools = ToolRegistry()
    tools.register(FailingTool())

    result = await tools.execute("failing", {"count": 1})

    assert result.is_error is True
    assert str(result) == f"no thanks{RETRY_HINT}"


async def test_the_retry_hint_is_added_only_once():
    class HintedTool(DemoTool):
        name = "hinted"

        async def execute(self, **kwargs: Any) -> ToolResult:
            return ToolResult.error(f"already hinted{RETRY_HINT}")

    tools = ToolRegistry()
    tools.register(HintedTool())

    result = await tools.execute("hinted", {"count": 1})

    assert str(result) == f"already hinted{RETRY_HINT}"


async def test_execute_normalises_a_plain_string_return():
    class StringTool(DemoTool):
        name = "strtool"

        async def execute(self, **kwargs: Any) -> ToolResult:
            return "plain"  # type: ignore[return-value]

    tools = ToolRegistry()
    tools.register(StringTool())

    result = await tools.execute("strtool", {"count": 1})

    assert result == "plain"
    assert result.is_error is False


async def test_calculator_evaluates_arithmetic():
    tool = CalculatorTool()

    assert await tool.execute(expression="(12+8)*3") == "(12+8)*3 = 60"
    assert await tool.execute(expression="2**10") == "2**10 = 1024"
    assert await tool.execute(expression="-7 % 3") == "-7 % 3 = 2"
    assert await tool.execute(expression="7 // 2") == "7 // 2 = 3"


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("1 +", "not a valid expression"),
        ("1 / 0", "division by zero"),
        ("2 ** 900", "exponent must be at most 64"),
        ("'abc'", "only numbers are allowed"),
        ("len('abc')", "Call is not supported"),
        ("1 & 2", "operator BitAnd is not supported"),
        ("~1", "operator Invert is not supported"),
        ("1e300 ** 64", "the result is too large"),
    ],
)
async def test_calculator_reports_unusable_expressions(expression, message):
    result = await CalculatorTool().execute(expression=expression)

    assert result.is_error is True
    assert message in result
    assert "cannot evaluate" in result


async def test_current_time_returns_iso_and_rejects_unknown_zones():
    ok = await CurrentTimeTool().execute(timezone="Asia/Shanghai")
    local = await CurrentTimeTool().execute()
    bad = await CurrentTimeTool().execute(timezone="Mars/Olympus")

    assert ok.startswith("20") and ok.endswith("+08:00")
    assert local.is_error is False
    assert bad.is_error is True
    assert "unknown time zone" in bad


async def test_read_file_returns_numbered_lines(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    tool = ReadFileTool(tmp_path)

    result = await tool.execute(path="notes.txt")

    assert result.splitlines()[0] == "notes.txt (2 lines)"
    assert "   1 | alpha" in result
    assert "   2 | beta" in result


async def test_read_file_truncates_to_max_lines_and_reports_the_total(tmp_path):
    (tmp_path / "long.txt").write_text("\n".join(str(i) for i in range(5)), encoding="utf-8")

    result = await ReadFileTool(tmp_path).execute(path="long.txt", max_lines=2)

    assert "long.txt (5 lines), showing the first 2" in result
    assert "   2 | 1" in result
    assert "   3 |" not in result


@pytest.mark.parametrize("path", ["missing.txt", "."])
async def test_read_file_rejects_non_files(tmp_path, path):
    result = await ReadFileTool(tmp_path).execute(path=path)

    assert result.is_error is True
    assert "is not a file inside the workspace" in result


async def test_read_file_without_a_path_is_an_error(tmp_path):
    result = await ReadFileTool(tmp_path).execute()

    assert result.is_error is True
    assert "requires a 'path' parameter" in result


@pytest.mark.parametrize("path", ["../secrets.txt", "/etc/passwd"])
async def test_read_file_stays_inside_the_workspace(tmp_path, path):
    result = await ReadFileTool(tmp_path).execute(path=path)

    assert result.is_error is True
    assert "outside the configured workspace" in result or "must be relative" in result


def test_workspace_resolution_rejects_absolute_paths(tmp_path):
    with pytest.raises(WorkspaceError, match="must be relative"):
        resolve_in_workspace(tmp_path, "/etc/hosts")


async def test_read_file_reports_unreadable_files(tmp_path, monkeypatch):
    target = tmp_path / "data.txt"
    target.write_text("content", encoding="utf-8")

    def boom(*args: Any, **kwargs: Any) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)

    result = await ReadFileTool(tmp_path).execute(path="data.txt")

    assert result.is_error is True
    assert "could not read 'data.txt'" in result


async def test_search_local_finds_literal_matches(tmp_path):
    (tmp_path / "one.txt").write_text("nothing here\nAttention is all you need\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("attention again\n", encoding="utf-8")

    result = await SearchLocalTool(tmp_path).execute(query="attention")

    assert "one.txt:2: Attention is all you need" in result
    assert "two.txt:1: attention again" in result


async def test_search_local_respects_the_limit_and_reports_no_matches(tmp_path):
    (tmp_path / "many.txt").write_text("hit\nhit\nhit\n", encoding="utf-8")

    limited = await SearchLocalTool(tmp_path).execute(query="hit", limit=2)
    empty = await SearchLocalTool(tmp_path).execute(query="absent")

    assert limited.splitlines() == ["many.txt:1: hit", "many.txt:2: hit"]
    assert empty.is_error is False
    assert "No matches for 'absent'" in empty


async def test_search_local_skips_hidden_oversized_and_unreadable_files(tmp_path, monkeypatch):
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("needle", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "cache.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "big.txt").write_text("needle" * 60_000, encoding="utf-8")
    (tmp_path / "unreadable.txt").write_text("needle", encoding="utf-8")

    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "unreadable.txt":
            raise OSError("nope")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    result = await SearchLocalTool(tmp_path).execute(query="needle")

    assert result == f"No matches for 'needle' under {tmp_path.resolve()}."


async def test_search_local_reports_a_missing_workspace(tmp_path):
    result = await SearchLocalTool(tmp_path / "absent").execute(query="x")

    assert result.is_error is True
    assert "does not exist" in result


def test_default_registry_registers_the_four_phase_two_tools(tmp_path):
    tools = build_default_registry(AgentSettings(workspace=tmp_path))

    assert sorted(tools.tool_names) == [
        "calculator",
        "current_time",
        "read_file",
        "search_local",
    ]
    assert all(
        entry["function"]["parameters"]["type"] == "object" for entry in tools.get_definitions()
    )
