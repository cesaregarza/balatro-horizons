"""Table-driven transport parity and declared capability validation."""

import json

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


def test_only_decision_end_clears_captured_provider_turn():
    ctx, _ = decision_context(project(FakeGame().observe_private()), [])
    transport = DirectProvider(configured("openai"), Limits())
    transport.request(ctx, [])
    response = {
        "status": "incomplete",
        "output": [{
            "type": "function_call", "call_id": "call_1", "name": "inspect_state",
            "arguments": "{}",
        }],
    }
    with pytest.raises(ProtocolFailure, match="PROVIDER_RESPONSE_INCOMPLETE"):
        transport.parse(response)
    assert transport.last_provider_turn is not None
    transport.request(ctx, [])
    assert transport.last_provider_turn is not None
    transport.on_decision_end()
    assert transport.last_provider_turn is None
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
