"""Authoritative validation for the complete canonical tool-schema vocabulary.

This validator deliberately rejects unsupported schema keywords during preflight;
adding a catalog constraint cannot silently weaken local validation.
"""

import math
import re

from balatro_horizons.harness.transport.errors import ProtocolFailure

KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "anyOf",
    "enum", "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern",
    "description",
})
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def validate_catalog(tools):
    if not isinstance(tools, list) or not tools:
        raise ValueError("INVALID_TOOL_CATALOG")
    names = set()
    for tool in tools:
        if (not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
                or not tool["name"] or tool["name"] in names
                or not isinstance(tool.get("description"), str)):
            raise ValueError("INVALID_TOOL_CATALOG")
        names.add(tool["name"])
        schema = tool.get("parameters")
        check_schema(schema)
        if schema.get("type") != "object":
            raise ValueError("INVALID_TOOL_SCHEMA")


def check_schema(schema):
    if not isinstance(schema, dict) or set(schema) - KEYWORDS:
        raise ValueError("UNSUPPORTED_TOOL_SCHEMA")
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    if not isinstance(types, list) or any(item not in TYPES for item in types):
        raise ValueError("INVALID_TOOL_SCHEMA")
    if not types and "anyOf" not in schema:
        raise ValueError("INVALID_TOOL_SCHEMA")
    if "object" in types:
        props, required = schema.get("properties"), schema.get("required")
        if (not isinstance(props, dict) or not isinstance(required, list)
                or set(required) != set(props) or schema.get("additionalProperties") is not False):
            raise ValueError("INVALID_TOOL_SCHEMA")
        for child in props.values():
            check_schema(child)
    if "array" in types:
        check_schema(schema.get("items"))
    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        if not isinstance(alternatives, list) or not alternatives:
            raise ValueError("INVALID_TOOL_SCHEMA")
        for child in alternatives:
            check_schema(child)
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error):
            raise ValueError("INVALID_TOOL_SCHEMA") from None


def validate_arguments(value, schema, path="$", *, root=True):
    if root and not isinstance(value, dict):
        raise ProtocolFailure("TOOL_ARGUMENTS_MUST_BE_OBJECT")
    alternatives = schema.get("anyOf")
    if alternatives is not None:
        _alternatives(value, alternatives, path)
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else [expected]
    if expected is not None and not any(_is_type(value, kind) for kind in types):
        _invalid(path, "type")
    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
        _invalid(path, "enum")
    if "const" in schema and not _equal(value, schema["const"]):
        _invalid(path, "const")
    if isinstance(value, dict):
        _object(value, schema, path)
    elif isinstance(value, list):
        _array(value, schema, path)
    elif isinstance(value, str):
        _bounds(len(value), schema, path, "minLength", "maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            _invalid(path, "pattern")
    elif type(value) in (int, float):
        _bounds(value, schema, path, "minimum", "maximum")
        for key, rejected in (("exclusiveMinimum", value <= schema.get("exclusiveMinimum", -math.inf)),
                              ("exclusiveMaximum", value >= schema.get("exclusiveMaximum", math.inf))):
            if key in schema and rejected:
                _invalid(path, key)


def _alternatives(value, alternatives, path):
    typed_errors = []
    for child in alternatives:
        try:
            validate_arguments(value, child, path, root=False)
            return
        except ProtocolFailure as error:
            types = child.get("type", [])
            types = [types] if isinstance(types, str) else types
            if any(_is_type(value, kind) for kind in types):
                typed_errors.append(error)
    # A nullable object has one applicable branch: retain its actionable error
    # rather than hiding a missing property behind a generic anyOf failure.
    if len(typed_errors) == 1:
        raise typed_errors[0]
    _invalid(path, "anyOf")


def _is_type(value, kind):
    if kind == "number":
        return type(value) in (int, float) and math.isfinite(value)
    expected = {"object": dict, "array": list, "string": str, "integer": int,
                "boolean": bool, "null": type(None)}.get(kind)
    return type(value) is expected


def _equal(left, right):
    return type(left) is type(right) and left == right


def _object(value, schema, path):
    props = schema.get("properties", {})
    missing = sorted(set(schema.get("required", [])) - value.keys())
    unexpected = sorted(value.keys() - props.keys()) if schema.get("additionalProperties") is False else []
    if missing or unexpected:
        _invalid(path, "required" if missing else "additionalProperties",
                 missing_keys=missing, unexpected_keys=unexpected)
    for name, child in props.items():
        if name in value:
            validate_arguments(value[name], child, f"{path}.{name}", root=False)


def _array(value, schema, path):
    _bounds(len(value), schema, path, "minItems", "maxItems")
    if schema.get("uniqueItems") and any(value[index] in value[:index] for index in range(len(value))):
        _invalid(path, "uniqueItems")
    if "items" in schema:
        for index, item in enumerate(value):
            validate_arguments(item, schema["items"], f"{path}[{index}]", root=False)


def _bounds(value, schema, path, lower, upper):
    if lower in schema and value < schema[lower]:
        _invalid(path, lower)
    if upper in schema and value > schema[upper]:
        _invalid(path, upper)


def _invalid(path, constraint, **details):
    raise ProtocolFailure("INVALID_TOOL_ARGUMENTS", argument_path=path, constraint=constraint, **details)
