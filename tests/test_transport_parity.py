"""Table-driven transport parity and declared capability validation."""

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError
from test_boundary import project

from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.transport import (
    DirectProvider,
    ProtocolFailure,
    context_payload,
    provider_spec,
)


def configured(provider, **settings):
    return ModelConfig(
        provider=provider,
        model="gpt-5.6-luna" if provider == "openai" else "claude-test",
        input_usd_per_million=1,
        output_usd_per_million=1,
        cached_input_usd_per_million=1 if provider == "openai" else None,
        cache_write_input_usd_per_million=1 if provider == "openai" else None,
        pricing_date="2026-09-20",
        settings=settings,
    )


def test_specs_share_prompt_messages_and_tool_catalog():
    ctx, exchanges = decision_context(project(FakeGame().observe_private()), [])
    openai = context_payload(ctx, exchanges, "openai")
    anthropic = context_payload(ctx, exchanges, "anthropic")

    assert openai["input"][0]["content"][0]["text"] == anthropic["system"]
    assert json.loads(openai["input"][1]["content"]) == json.loads(
        anthropic["messages"][0]["content"]
    )
    assert {tool["name"] for tool in openai["tools"]} == {
        tool["name"] for tool in anthropic["tools"]
    }


def test_specs_declare_the_table_driven_parse_fields():
    required = {
        "terminal_field",
        "terminal_map",
        "call_item_type",
        "id_field",
        "args_field",
        "result_item_type",
        "cache_options",
    }
    for provider in ("openai", "anthropic"):
        assert required <= vars(provider_spec(provider)).keys()


def test_request_preserves_a_valid_turn_until_decision_end():
    ctx, _ = decision_context(project(FakeGame().observe_private()), [])
    transport = DirectProvider(configured("openai"), Limits())
    transport.request(ctx, [])
    response = {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "calculate",
                "arguments": '{"expression":"2+2"}',
            }
        ],
    }
    assert transport.parse(response) == {"kind": "arithmetic", "expression": "2+2"}
    assert transport.last_provider_turn is not None
    transport.request(ctx, [])
    assert transport.last_provider_turn is not None
    transport.on_decision_end()
    assert transport.last_provider_turn is None
    transport.client.close()


OMITTED = object()
VALID_CALL = {"name": "calculate", "arguments": {"expression": "2+2"}}
VALID_OPERATION = {"kind": "arithmetic", "expression": "2+2"}


@dataclass(frozen=True)
class ParseCase:
    name: str
    provider: str
    code: str | None
    last_call: dict[str, Any] | None
    retains_turn: bool
    terminal: Any = OMITTED
    call_count: int = 1
    available: bool = True
    identifier: str = "present"
    arguments: str = "valid"
    native_item: bool = False
    call_status: str = "completed"
    expected_details: dict[str, Any] | None = None


def parse_case(name, provider, code=None, last_call=VALID_CALL, retains_turn=True, **values):
    return ParseCase(name, provider, code, last_call, retains_turn, **values)


PARSE_CASES = [
    parse_case("openai-terminal-missing", "openai"),
    parse_case("openai-terminal-completed", "openai", terminal="completed"),
    parse_case(
        "openai-terminal-incomplete",
        "openai",
        "PROVIDER_RESPONSE_INCOMPLETE",
        None,
        False,
        terminal="incomplete",
    ),
    parse_case(
        "openai-terminal-failed",
        "openai",
        "PROVIDER_RESPONSE_FAILED",
        None,
        False,
        terminal="failed",
    ),
    parse_case(
        "openai-terminal-cancelled",
        "openai",
        "PROVIDER_RESPONSE_FAILED",
        None,
        False,
        terminal="cancelled",
    ),
    parse_case(
        "openai-terminal-in-progress",
        "openai",
        "PROVIDER_RESPONSE_UNRESOLVED",
        None,
        False,
        terminal="in_progress",
    ),
    parse_case(
        "openai-terminal-queued",
        "openai",
        "PROVIDER_RESPONSE_UNRESOLVED",
        None,
        False,
        terminal="queued",
    ),
    parse_case(
        "openai-terminal-unknown",
        "openai",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        terminal="future_status",
    ),
    parse_case("anthropic-terminal-missing", "anthropic"),
    parse_case("anthropic-terminal-tool-use", "anthropic", terminal="tool_use"),
    parse_case(
        "anthropic-terminal-max-tokens",
        "anthropic",
        "PROVIDER_RESPONSE_INCOMPLETE",
        None,
        False,
        terminal="max_tokens",
    ),
    parse_case(
        "anthropic-terminal-context",
        "anthropic",
        "PROVIDER_CONTEXT_LIMIT",
        None,
        False,
        terminal="model_context_window_exceeded",
    ),
    parse_case(
        "anthropic-terminal-refusal",
        "anthropic",
        "PROVIDER_REFUSAL",
        None,
        True,
        terminal="refusal",
    ),
    parse_case(
        "anthropic-terminal-pause",
        "anthropic",
        "PROVIDER_RESPONSE_PAUSED",
        None,
        False,
        terminal="pause_turn",
    ),
    parse_case(
        "anthropic-terminal-unknown",
        "anthropic",
        "UNEXPECTED_PROVIDER_STOP",
        None,
        True,
        terminal="end_turn",
    ),
    parse_case(
        "openai-zero-calls",
        "openai",
        "NO_OPERATION",
        None,
        True,
        call_count=0,
        expected_details={},
    ),
    parse_case("openai-two-calls", "openai", "MULTIPLE_OPERATIONS", None, True, call_count=2),
    parse_case("anthropic-zero-calls", "anthropic", "NO_OPERATION", None, True, call_count=0),
    parse_case("anthropic-two-calls", "anthropic", "MULTIPLE_OPERATIONS", None, True, call_count=2),
    parse_case("openai-unavailable", "openai", "UNAVAILABLE_TOOL", None, True, available=False),
    parse_case(
        "anthropic-unavailable", "anthropic", "UNAVAILABLE_TOOL", None, True, available=False
    ),
    parse_case(
        "openai-id-missing",
        "openai",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        identifier="missing",
    ),
    parse_case(
        "openai-id-empty", "openai", "INVALID_PROVIDER_RESPONSE", None, False, identifier="empty"
    ),
    parse_case(
        "anthropic-id-missing",
        "anthropic",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        identifier="missing",
    ),
    parse_case(
        "anthropic-id-empty",
        "anthropic",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        identifier="empty",
    ),
    parse_case(
        "openai-arguments-malformed",
        "openai",
        "INVALID_OPERATION_JSON",
        None,
        True,
        arguments="malformed",
    ),
    parse_case(
        "openai-arguments-missing",
        "openai",
        "INVALID_OPERATION_JSON",
        None,
        True,
        arguments="missing",
    ),
    parse_case(
        "openai-arguments-wrong-type",
        "openai",
        "TOOL_ARGUMENTS_MUST_BE_OBJECT",
        {"name": "calculate", "arguments": []},
        True,
        arguments="wrong_type",
    ),
    parse_case(
        "anthropic-arguments-malformed",
        "anthropic",
        "TOOL_ARGUMENTS_MUST_BE_OBJECT",
        {"name": "calculate", "arguments": "malformed"},
        True,
        arguments="malformed",
    ),
    parse_case(
        "anthropic-arguments-missing",
        "anthropic",
        "TOOL_ARGUMENTS_MUST_BE_OBJECT",
        {"name": "calculate", "arguments": None},
        True,
        arguments="missing",
    ),
    parse_case(
        "anthropic-arguments-wrong-type",
        "anthropic",
        "TOOL_ARGUMENTS_MUST_BE_OBJECT",
        {"name": "calculate", "arguments": []},
        True,
        arguments="wrong_type",
    ),
    parse_case("openai-reasoning-success", "openai", native_item=True),
    parse_case(
        "openai-reasoning-no-call",
        "openai",
        "NO_OPERATION",
        None,
        True,
        call_count=0,
        native_item=True,
    ),
    parse_case("anthropic-thinking-success", "anthropic", native_item=True),
    parse_case(
        "anthropic-thinking-no-call",
        "anthropic",
        "NO_OPERATION",
        None,
        True,
        call_count=0,
        native_item=True,
    ),
    parse_case(
        "openai-incomplete-call-unavailable",
        "openai",
        "PROVIDER_RESPONSE_INCOMPLETE",
        None,
        False,
        available=False,
        call_status="incomplete",
    ),
    parse_case(
        "openai-unresolved-call-unavailable",
        "openai",
        "PROVIDER_RESPONSE_UNRESOLVED",
        None,
        False,
        available=False,
        call_status="in_progress",
    ),
    parse_case(
        "openai-missing-id-unavailable",
        "openai",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        available=False,
        identifier="missing",
    ),
    parse_case(
        "openai-empty-id-unavailable",
        "openai",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        available=False,
        identifier="empty",
    ),
    parse_case(
        "anthropic-missing-id-unavailable",
        "anthropic",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        available=False,
        identifier="missing",
    ),
    parse_case(
        "anthropic-empty-id-unavailable",
        "anthropic",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        available=False,
        identifier="empty",
    ),
    parse_case(
        "openai-incomplete-no-call",
        "openai",
        "PROVIDER_RESPONSE_INCOMPLETE",
        None,
        False,
        terminal="incomplete",
        call_count=0,
    ),
    parse_case(
        "openai-unknown-no-call",
        "openai",
        "INVALID_PROVIDER_RESPONSE",
        None,
        False,
        terminal="future_status",
        call_count=0,
    ),
    parse_case(
        "anthropic-max-no-call",
        "anthropic",
        "PROVIDER_RESPONSE_INCOMPLETE",
        None,
        False,
        terminal="max_tokens",
        call_count=0,
    ),
    parse_case(
        "anthropic-refusal-no-call",
        "anthropic",
        "PROVIDER_REFUSAL",
        None,
        True,
        terminal="refusal",
        call_count=0,
    ),
    parse_case(
        "anthropic-end-no-call",
        "anthropic",
        "NO_OPERATION",
        None,
        True,
        terminal="end_turn",
        call_count=0,
    ),
]


def provider_response(case):
    spec = provider_spec(case.provider)
    items = [_native_item(case.provider)] if case.native_item else []
    items.extend(_call(case, index) for index in range(case.call_count))
    response = {spec.items_field: items}
    if case.terminal is not OMITTED:
        response[spec.terminal_field] = case.terminal
    return response, items


def _native_item(provider):
    if provider == "openai":
        return {"type": "reasoning", "encrypted_content": "opaque"}
    return {"type": "thinking", "thinking": "opaque", "signature": "signed"}


def _call(case, index):
    spec = provider_spec(case.provider)
    call = {
        "type": spec.call_item_type,
        spec.id_field: f"call_{index}",
        "name": "calculate" if case.available else "not_allowed",
        spec.args_field: _arguments(case.provider, case.arguments),
    }
    if case.provider == "openai":
        call["status"] = case.call_status
    if case.identifier == "missing":
        call.pop(spec.id_field)
    elif case.identifier == "empty":
        call[spec.id_field] = ""
    if case.arguments == "missing":
        call.pop(spec.args_field)
    return call


def _arguments(provider, kind):
    if kind == "valid":
        return '{"expression":"2+2"}' if provider == "openai" else {"expression": "2+2"}
    if kind == "malformed":
        return "{" if provider == "openai" else "malformed"
    if kind == "wrong_type":
        return "[]" if provider == "openai" else []
    return None


@pytest.mark.parametrize("case", PARSE_CASES, ids=lambda case: case.name)
def test_parse_matrix_matches_frozen_provider_behavior(case):
    ctx, _ = decision_context(project(FakeGame().observe_private()), [])
    transport = DirectProvider(configured(case.provider), Limits())
    try:
        transport.request(ctx, [])
        response, items = provider_response(case)
        if case.code is None:
            assert transport.parse(response) == VALID_OPERATION
        else:
            with pytest.raises(ProtocolFailure) as error:
                transport.parse(response)
            assert type(error.value) is ProtocolFailure
            assert error.value.code == case.code
            if case.expected_details is not None:
                assert error.value.details == case.expected_details
        assert transport.last_tool_call == case.last_call
        expected_turn = {
            "version": "provider_turn_v1",
            "provider": case.provider,
            "items": deepcopy(items),
        }
        assert transport.last_provider_turn == (expected_turn if case.retains_turn else None)
    finally:
        transport.client.close()


def test_uncategorized_openai_cache_creation_retains_reservation():
    values = configured("openai").model_dump()
    values.update(cached_input_usd_per_million=None, cache_write_input_usd_per_million=None)
    transport = DirectProvider(ModelConfig.model_validate(values), Limits())
    try:
        response = {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 10,
                "cache_creation_input_tokens": 1,
            }
        }
        assert transport.usage_cost(response, 0.25) == 0.25
    finally:
        transport.client.close()


@pytest.mark.parametrize(
    ("provider", "settings"),
    [
        ("openai", {"reasoning_effort": "minimal"}),
        ("openai", {"thinking_budget": 1024}),
        ("anthropic", {"reasoning_effort": "medium"}),
    ],
)
def test_unsupported_settings_fail_per_provider(provider, settings):
    with pytest.raises(ValidationError):
        configured(provider, **settings)
