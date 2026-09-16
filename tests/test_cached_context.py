"""Stable cache boundaries without private state or weaker action validation."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_boundary import project

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.agents.protocol import Operation, decision_context
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure, ProviderFailure
from balatro_horizons.agents.skills import load_guide
from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.engine.fake import FakeGame


def model(provider="openai"):
    return ModelConfig(
        provider=provider,
        model="gpt-5.6-terra" if provider == "openai" else "mock",
        input_usd_per_million=2,
        output_usd_per_million=12,
        pricing_date="2026-09-15",
        cached_input_usd_per_million=0.2 if provider == "openai" else None,
        cache_write_input_usd_per_million=2.5 if provider == "openai" else None,
        settings={"harness_interface": "tools_v4"},
    )


def request(obs, exchanges=(), provider="openai"):
    ctx, delivered = decision_context(obs, exchanges, interface="tools_v4", skills=load_guide()[1])
    policy = DirectProvider(model(provider), Limits())
    return policy, policy.request(ctx, delivered)


def prefix(body):
    return body["tools"], body["input"][0]


def test_prefix_stays_identical_across_phases_ids_and_helper_calls():
    game = FakeGame()
    obs = project(game.observe_private())
    _, first = request(obs)
    game.phase = "SELECTING_HAND"
    other = project(game.observe_private())
    other.observation_id = 19
    other.memory = "changing public memory"
    for card in other.state.hand:
        card.id = "new-" + card.id
    _, second = request(other)
    assert prefix(first) == prefix(second)
    assert first["tool_choice"] != second["tool_choice"]
    assert "observation_id" in first["input"][1]["content"]
    assert "changing public memory" in second["input"][1]["content"]
    args = {"section": "hand", "offset": 0}
    exchange = {
        "operation": {"kind": "inspect_page", **args},
        "tool_call": {"name": "inspect_state", "arguments": args},
        "result": {"content": "changing helper output", "game_advanced": False},
    }
    _, third = request(other, [exchange])
    assert prefix(second) == prefix(third)
    assert "changing helper output" in json.dumps(third["input"][2:])
    assert third["prompt_cache_options"] == {"mode": "explicit"}
    assert first["input"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert "previous_response_id" not in third
    assert "instructions" not in third
    assert json.dumps(third).count('"prompt_cache_breakpoint"') == 1
    assert len(json.dumps(third, ensure_ascii=False).encode()) + 4096 <= 32768


def test_provider_parity_and_inactive_tool_rejection():
    obs = project(FakeGame().observe_private())
    openai, a = request(obs)
    anthropic, b = request(obs, provider="anthropic")
    assert a["input"][0]["content"][0]["text"] == b["system"]
    assert a["input"][1:] == b["messages"]
    assert [(t["name"], t["description"], t["parameters"]) for t in a["tools"]] == [
        (t["name"], t["description"], t["input_schema"]) for t in b["tools"]
    ]
    assert openai.available_tools == anthropic.available_tools
    assert "buy" in {t["name"] for t in a["tools"]}
    assert "buy" not in openai.available_tools
    with pytest.raises(ProtocolFailure):
        openai.parse(
            {
                "status": "completed",
                "output": [{"type": "function_call", "name": "buy", "arguments": "{}"}],
            }
        )
    with pytest.raises(ProtocolFailure):
        anthropic.parse({"content": [{"type": "tool_use", "name": "buy", "input": {}}]})


def test_static_schemas_do_not_weaken_current_id_validation():
    game = FakeGame()
    obs = project(game.observe_private())
    before = deepcopy(game.observe_private())
    policy, _ = request(obs)
    args = {
        "observation_id": obs.observation_id + 1,
        "blind_id": obs.state.revealed_blinds[0].id,
        "memory_update": None,
        "decision_note": None,
    }

    def parsed():
        return Operation.validate_python(
            policy.parse(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "select_blind",
                            "arguments": json.dumps(args),
                        }
                    ],
                }
            )
        )

    with pytest.raises(InvalidAction, match="STALE_OBSERVATION"):
        validate_action(parsed().envelope, obs)
    args["observation_id"] = obs.observation_id
    args["blind_id"] = "stale-card-handle"
    with pytest.raises(InvalidAction, match="UNKNOWN_BLIND"):
        validate_action(parsed().envelope, obs)
    assert game.observe_private() == before


def test_hidden_card_noninterference_includes_cache_prefix():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    left = game.observe_private()
    for card in left["visible"]["hand"]:
        card["face_down"] = True
    right = deepcopy(left)
    for card in right["visible"]["hand"]:
        card.update(native_id="hidden-" + card["native_id"], label="PRIVATE_SECRET", rank="K")
    assert request(project(left))[1] == request(project(right))[1]


def test_disjoint_input_categories_and_worst_case_reservation():
    policy = DirectProvider(model(), Limits())
    assert policy.model.maximum_input_usd_per_million == 2.5
    response = {
        "usage": {
            "input_tokens": 5000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 3000, "cache_write_tokens": 1000},
        }
    }
    assert policy.usage_cost(response, 0.2) == pytest.approx(
        (1000 * 2 + 3000 * 0.2 + 1000 * 2.5 + 100 * 12) / 1e6
    )
    for details in (
        None,
        {},
        {"cached_tokens": 3000},
        {"cached_tokens": 3000, "cache_write_tokens": 3000},
        {"cached_tokens": True, "cache_write_tokens": 0},
        {"cached_tokens": 0, "cache_write_tokens": -1},
    ):
        response["usage"]["input_tokens_details"] = details
        assert policy.usage_cost(response, 0.2) == 0.2


def test_explicit_caching_requires_supported_model_rates_and_context_tier():
    obs = project(FakeGame().observe_private())
    ctx, exchanges = decision_context(obs, [], interface="tools_v4")
    cfg = model()
    cfg.model = "gpt-5.4"
    with pytest.raises(ProviderFailure, match="EXPLICIT_CACHE_REQUIRES"):
        DirectProvider(cfg, Limits()).request(ctx, exchanges)
    cfg = model()
    cfg.cached_input_usd_per_million = cfg.cache_write_input_usd_per_million = None
    with pytest.raises(ProviderFailure, match="EXPLICIT_CACHE_PRICING_REQUIRED"):
        DirectProvider(cfg, Limits()).request(ctx, exchanges)
    with pytest.raises(ProviderFailure, match="CACHE_LONG_CONTEXT"):
        DirectProvider(model(), Limits(max_input_tokens_per_call=272001)).request(ctx, exchanges)
    with pytest.raises(ValidationError, match="configure both"):
        ModelConfig.model_validate({**model().model_dump(), "cached_input_usd_per_million": None})
