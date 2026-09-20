"""The tool contract: schema, safe casts, validation and execution.

Mirrors upstream ``agent/tools/base.py`` (``ToolResult``, ``Tool.to_schema``,
``cast_params`` / ``validate_params``, ``Schema.validate_json_schema_value``)
with two deliberate simplifications:

* the JSON Schema subset is the one the built-in tools actually use (types,
  ``enum``, ``minimum`` / ``maximum``, ``minLength`` / ``maxLength``,
  ``required``, ``additionalProperties``, nested ``properties`` / ``items``);
* the ``@tool_parameters`` decorator is dropped — four tools are cheaper to read
  with an explicit ``parameters`` property than with class rewriting.

Phase 3 splits contract from convenience: :class:`BaseTool` is the structural
type the registry and the runner depend on, while :class:`Tool` is an ABC that
implements it so a built-in tool only has to declare ``parameters`` and
``execute`` (PLAN 3.4, ADR-0007).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Protocol, runtime_checkable

__all__ = ["BaseTool", "Tool", "ToolResult", "resolve_type", "validate_schema_value"]

_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}

_BOOL_TRUE: frozenset[str] = frozenset({"true", "1", "yes"})
_BOOL_FALSE: frozenset[str] = frozenset({"false", "0", "no"})


class ToolResult(str):
    """String-compatible tool output with a structured error flag.

    Inheriting from ``str`` keeps tool observations usable everywhere a plain
    string is expected (messages, JSONL, prompt building) while ``is_error``
    lets the runner tell "the tool failed" from "the tool returned this text".
    """

    is_error: bool

    def __new__(cls, content: str, *, is_error: bool = False) -> ToolResult:
        obj = str.__new__(cls, content)
        obj.is_error = is_error
        return obj

    @classmethod
    def error(cls, content: str) -> ToolResult:
        """Build a failing tool result."""
        return cls(content, is_error=True)


def resolve_type(schema_type: Any) -> str | None:
    """Pick the non-null type from JSON Schema unions like ``["string", "null"]``."""
    if isinstance(schema_type, list):
        return next((item for item in schema_type if item != "null"), None)
    return schema_type if isinstance(schema_type, str) else None


def subpath(path: str, key: str) -> str:
    """Build a dotted path for error messages (``"paper.title"``)."""
    return f"{path}.{key}" if path else key


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    python_type = _JSON_TYPE_MAP.get(expected)
    return True if python_type is None else isinstance(value, python_type)


def _is_nullable(schema: Mapping[str, Any]) -> bool:
    raw_type = schema.get("type")
    if isinstance(raw_type, list) and "null" in raw_type:
        return True
    return bool(schema.get("nullable", False))


def _validate_object(value: dict[str, Any], schema: Mapping[str, Any], path: str) -> list[str]:
    properties = schema.get("properties") or {}
    additional = schema.get("additionalProperties", True)
    errors = [
        f"missing required {subpath(path, key)}"
        for key in (schema.get("required") or [])
        if key not in value
    ]
    for key, item in value.items():
        if key in properties:
            errors.extend(validate_schema_value(item, properties[key], subpath(path, key)))
        elif additional is False:
            errors.append(f"unexpected parameter {subpath(path, key)}")
        elif isinstance(additional, dict):
            errors.extend(validate_schema_value(item, additional, subpath(path, key)))
    return errors


def _validate_array(value: list[Any], schema: Mapping[str, Any], path: str) -> list[str]:
    items = schema.get("items")
    if not isinstance(items, dict):
        return []
    prefix = f"{path}[{{}}]" if path else "[{}]"
    return [
        error
        for index, item in enumerate(value)
        for error in validate_schema_value(item, items, prefix.format(index))
    ]


def validate_schema_value(value: Any, schema: Mapping[str, Any], path: str = "") -> list[str]:
    """Validate one value against a JSON Schema fragment; returns error messages."""
    expected = resolve_type(schema.get("type"))
    label = path or "parameter"
    if value is None and _is_nullable(schema):
        return []
    if expected is not None and not _matches_type(value, expected):
        return [f"{label} should be {expected}"]

    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{label} must be one of {list(schema['enum'])}")
    if expected in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{label} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{label} must be <= {schema['maximum']}")
    if expected == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{label} must be at least {schema['minLength']} chars")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{label} must be at most {schema['maxLength']} chars")
    if expected == "object":
        errors.extend(_validate_object(value, schema, path))
    if expected == "array":
        errors.extend(_validate_array(value, schema, path))
    return errors


@runtime_checkable
class BaseTool(Protocol):
    """What the registry, the runner and the model need from a tool."""

    name: str
    description: str
    read_only: bool
    exclusive: bool

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for the tool arguments."""
        ...

    @property
    def concurrency_safe(self) -> bool:
        """Whether this tool may run in the same batch as other safe tools."""
        ...

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Run the tool; failures come back as ``ToolResult.error(...)``."""
        ...

    def to_schema(self) -> dict[str, Any]:
        """The OpenAI ``tools`` entry for this tool."""
        ...

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Apply safe, schema-driven casts before validation."""
        ...

    def validate_params(self, params: Any) -> list[str]:
        """Validate arguments against the schema; empty list means valid."""
        ...


class Tool(ABC):
    """One capability the model may call.

    The convenience implementation of :class:`BaseTool`: subclasses declare
    ``parameters`` and ``execute``, and inherit schema, casting and validation.
    Nothing has to inherit from it — the registry only looks at the shape.

    ``read_only`` / ``exclusive`` feed :attr:`concurrency_safe`, which the runner
    uses to decide whether calls may run in the same ``asyncio.gather`` batch
    (upstream: ``agent/tools/execution.py:_partition_tool_batches``).
    """

    name: str
    description: str
    read_only: bool = False
    exclusive: bool = False

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for the tool arguments (a fresh copy on every access)."""
        ...

    @property
    def concurrency_safe(self) -> bool:
        """Whether this tool may run alongside other concurrency-safe tools."""
        return self.read_only and not self.exclusive

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Run the tool. Failures are returned, not raised: ``ToolResult.error(...)``."""
        ...

    def to_schema(self) -> dict[str, Any]:
        """The OpenAI ``tools`` entry for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Apply safe, schema-driven casts before validation (``"3"`` → ``3``)."""
        schema = self.parameters
        if schema.get("type", "object") != "object":
            return params
        return _cast_object(params, schema)

    def validate_params(self, params: Any) -> list[str]:
        """Validate arguments against the schema; empty list means valid."""
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        schema = self.parameters
        if schema.get("type", "object") != "object":
            raise ValueError(f"tool schema must be an object type, got {schema.get('type')!r}")
        return validate_schema_value(params, schema)


def _cast_object(value: dict[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties") or {}
    additional = schema.get("additionalProperties")
    casted: dict[str, Any] = {}
    for key, item in value.items():
        if key in properties:
            casted[key] = _cast_value(item, properties[key])
        elif isinstance(additional, dict):
            casted[key] = _cast_value(item, additional)
        else:
            casted[key] = item
    return casted


def _cast_value(value: Any, schema: Mapping[str, Any]) -> Any:
    """Cast one value towards the schema's declared type, leaving it alone when unsure."""
    expected = resolve_type(schema.get("type"))

    if expected == "integer" and isinstance(value, str):
        return _try(lambda: int(value), value)
    if expected == "number" and isinstance(value, str):
        return _try(lambda: float(value), value)
    if expected == "boolean" and isinstance(value, str):
        lowered = value.lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        return value
    if expected == "string" and value is not None and not isinstance(value, str):
        return str(value)
    if expected == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [_cast_value(item, schema["items"]) for item in value]
    if expected == "object" and isinstance(value, dict):
        return _cast_object(value, schema)
    return value


def _try(cast: Any, fallback: Any) -> Any:
    """Run ``cast()``, keeping ``fallback`` when the cast cannot succeed.

    Validation reports the original value's problem; a failed cast must not
    turn "3.5 is not an integer" into a type error inside the registry.
    """
    try:
        return cast()
    except (TypeError, ValueError):
        return fallback


def schema_copy(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return a defensive copy of a module-level schema constant."""
    return deepcopy(dict(schema))
