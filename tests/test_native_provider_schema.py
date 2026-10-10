"""The unchanged full catalog is validated locally before any operation decode."""

import json
from copy import deepcopy

import pytest
from test_claude_support import context
from test_provider_continuations import model

from balatro_horizons.config import Limits
from balatro_horizons.harness.context.render import tool_catalog
from balatro_horizons.harness.transport import DirectProvider, ProtocolFailure, validate_runtime
from balatro_horizons.harness.transport.schema import validate_arguments

TOOLS = tool_catalog([{"name": "balatro-test"}])


def example(schema):
    if "anyOf" in schema:
        return example(schema["anyOf"][0])
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema["type"]
    kind = kind[0] if isinstance(kind, list) else kind
    if kind == "object":
        return {name: example(child) for name, child in schema["properties"].items()}
    if kind == "array":
        return [example(schema["items"]) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return "a" * max(1, schema.get("minLength", 0))
    if kind == "integer":
        return max(1, schema.get("minimum", 0))
    if kind == "null":
        return None
    raise AssertionError(kind)


def native_call(provider, name, arguments):
    if provider == "openai":
        return {"status": "completed", "output": [{
            "type": "function_call", "call_id": "call_1", "name": name,
            "arguments": json.dumps(arguments),
        }]}
    return {"stop_reason": "tool_use", "content": [{
        "type": "tool_use", "id": "toolu_1", "name": name, "input": arguments,
    }]}


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_full_catalog_native_schemas_are_exact_and_preflight_is_local(provider, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    validate_runtime(model(provider), Limits(), TOOLS)
    ctx = context()
    ctx.tools = deepcopy(TOOLS)
    policy = DirectProvider(model(provider), Limits())
    body = policy.request(ctx, [])
    assert len(body["tools"]) == len(TOOLS) == 24
    for canonical, native in zip(TOOLS, body["tools"], strict=True):
        assert native["name"] == canonical["name"]
        assert native["description"] == canonical["description"]
        field = "parameters" if provider == "openai" else "input_schema"
        assert native[field] == canonical["parameters"]
        assert native["strict"] is (provider == "openai")
    assert body["stream"] is True


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool["name"])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_all_tools_validate_required_extra_and_types_before_decode(provider, tool):
    ctx = context()
    ctx.tools, ctx.allowed_tools = deepcopy(TOOLS), [item["name"] for item in TOOLS]
    policy = DirectProvider(model(provider), Limits())
    policy.request(ctx, [])
    schema, args = tool["parameters"], example(tool["parameters"])
    validate_arguments(args, schema)
    # Keep this test about schema/decode; valid IDs resolve through the ordinary reference map.
    if args.get("episode_id") is not None:
        ctx.model_references.project({"episode_id": "a" * 32})
    operation = policy.parse(native_call(provider, tool["name"], args))
    assert isinstance(operation, dict) and "kind" in operation
    mutations = [{**args, "UNDECLARED": True}]
    for field in schema["required"]:
        missing = deepcopy(args)
        missing.pop(field)
        mutations.extend([missing, {**args, field: {"wrong": "type"}}])
    for invalid in mutations:
        with pytest.raises(ProtocolFailure, match="INVALID_TOOL_ARGUMENTS"):
            policy.parse(native_call(provider, tool["name"], invalid))


@pytest.mark.parametrize("name,args", [
    ("select_blind", {"observation_id": True, "blind_id": 1, "decision_note": None, "note_update": None}),
    ("select_blind", {"observation_id": 0, "blind_id": True, "decision_note": None, "note_update": None}),
    ("play_hand", {"observation_id": 0, "card_ids": [True], "decision_note": None, "note_update": None}),
    ("play_hand", {"observation_id": 0, "card_ids": "1", "decision_note": None, "note_update": None}),
    ("read_history", {"offset": -1, "limit": 1}),
    ("read_history", {"offset": 0, "limit": 999999}),
    ("inspect_state", {"section": "private", "offset": 0}),
    ("set_run_note", {"key": "", "text": "text"}),
    ("calculate", {"expression": "x" * 10000}),
    ("select_blind", {"observation_id": 0, "blind_id": 1, "decision_note": None,
                       "note_update": {"key": "ok", "text": None, "extra": 1}}),
])
def test_catalog_constraints_cannot_be_coerced(name, args):
    schema = next(tool["parameters"] for tool in TOOLS if tool["name"] == name)
    with pytest.raises(ProtocolFailure):
        validate_arguments(args, schema)


def test_unknown_schema_constraint_fails_preflight():
    tools = deepcopy(TOOLS)
    tools[0]["parameters"]["madeUpConstraint"] = True
    with pytest.raises(ValueError, match="UNSUPPORTED_TOOL_SCHEMA"):
        validate_runtime(model("anthropic"), Limits(), tools)


def test_schema_array_bounds_and_game_card_legality_are_separate():
    schema = {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2}
    for value in ([], [1, 2, 3]):
        with pytest.raises(ProtocolFailure):
            validate_arguments(value, schema, root=False)
    play = next(tool["parameters"] for tool in TOOLS if tool["name"] == "play_hand")
    # The runner owns hand-selection legality; do not invent absent schema constraints.
    validate_arguments({"observation_id": 0, "card_ids": [], "decision_note": None,
                        "note_update": None}, play)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_current_availability_is_used_even_with_frozen_initial_context(provider):
    ctx = context()
    policy = DirectProvider(model(provider), Limits())
    first = policy.request(ctx, [])
    ctx.provider_initial_content = (first["input"][1]["content"] if provider == "openai"
                                    else first["messages"][0]["content"])
    ctx.allowed_tools = ["abort_run"]
    policy.request(ctx, [])
    with pytest.raises(ProtocolFailure, match="UNAVAILABLE_TOOL"):
        policy.parse(native_call(provider, "calculate", {"expression": "1+1"}))


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("nested", [False, True])
def test_schema_feedback_names_missing_and_unexpected_keys_including_nullable_objects(provider, nested):
    ctx = context()
    ctx.tools, ctx.allowed_tools = deepcopy(TOOLS), [tool["name"] for tool in TOOLS]
    policy = DirectProvider(model(provider), Limits())
    args = {"observation_id": 0, "blind_id": 1, "decision_note": None,
            "note_update": {"key": "plan", "surprise": "not echoed"}} if nested else {
        "observation_id": 0, "blind_id": 1, "surprise": "not echoed",
    }
    try:
        policy.request(ctx, [])
        with pytest.raises(ProtocolFailure, match="INVALID_TOOL_ARGUMENTS") as caught:
            policy.parse(native_call(provider, "select_blind", args))
        assert caught.value.details == {
            "argument_path": "$.note_update" if nested else "$", "constraint": "required",
            "missing_keys": ["text"] if nested else ["decision_note", "note_update"],
            "unexpected_keys": ["surprise"],
        }
        assert "not echoed" not in json.dumps(caught.value.details)
    finally:
        policy.client.close()
