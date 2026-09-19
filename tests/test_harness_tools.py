"""Single-harness named-tool integration; mocked providers and synthetic mechanics only."""

import json
from copy import deepcopy

import httpx
import pytest
from provider_transport import with_input_count
from test_boundary import project

from balatro_horizons.agents.protocol import Operation, context
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure
from balatro_horizons.agents.tool_interface import ACTION_MODELS, decode_tool
from balatro_horizons.config import ROOT, ModelConfig, load_config
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.review.service import ReviewService
from balatro_horizons.runner import Runner


def config_for(provider="openai"):
    config = load_config(ROOT / "configs/luna-smoke.yaml")
    config.budgets.paid_calls_enabled = True
    if provider == "anthropic":
        config.models["luna"] = ModelConfig(
            provider="anthropic",
            model="mock-anthropic",
            input_usd_per_million=0.2,
            output_usd_per_million=1.2,
            pricing_date="2026-09-14",
            settings={},
        )
    return config


def test_catalog_is_static_while_allowed_actions_follow_the_observation():
    game = FakeGame()
    blind = context(project(game.observe_private()))
    catalog = {tool["name"]: tool for tool in blind["tools"]}
    assert set(ACTION_MODELS) <= catalog.keys()
    assert "select_blind" in blind["allowed_tools"]
    assert "play_hand" not in blind["allowed_tools"]
    assert catalog["inspect_state"]["parameters"]["required"] == ["section", "offset"]
    for definition in catalog.values():
        schema = definition["parameters"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert "$defs" not in schema and "oneOf" not in schema

    game.phase = "SELECTING_HAND"
    hand = context(project(game.observe_private()))
    assert blind["tools"] == hand["tools"]
    assert "select_blind" not in hand["allowed_tools"]
    assert {"play_hand", "discard"} <= set(hand["allowed_tools"])


def test_flat_decode_accepts_only_the_current_tool_shapes():
    for args in ({"kind": "abort"}, {"type": "discard"}, {"envelope": {}}):
        with pytest.raises(ValueError, match="UNEXPECTED_TOOL_WRAPPER"):
            decode_tool("play_hand", args)
    with pytest.raises(ValueError, match="LEGACY_MEMORY_UPDATE_NOT_ALLOWED"):
        decode_tool("cash_out", {"observation_id": 2, "memory_update": "old"})
    with pytest.raises(ValueError, match="EXPECTED_PAGED_INSPECTION"):
        decode_tool("inspect_state", {"sections": ["hand"]})

    inspect = decode_tool("inspect_state", {"section": "hand", "offset": 0})
    assert inspect == {"kind": "inspect_page", "section": "hand", "offset": 0}
    action = decode_tool(
        "play_hand",
        {
            "observation_id": 2,
            "card_ids": ["a"],
            "decision_note": "note",
            "note_update": {"key": "plan", "text": "pair"},
        },
    )
    parsed = Operation.validate_python(action)
    assert parsed.envelope.decision_note == "note"
    assert parsed.note_update.key == "plan"


def test_provider_schema_parity_and_unavailable_tool_rejection():
    ctx = context(project(FakeGame().observe_private()))
    policies = [
        DirectProvider(config_for(provider).models["luna"], config_for(provider).budgets)
        for provider in ("openai", "anthropic")
    ]
    try:
        openai, anthropic = [policy.request(ctx, []) for policy in policies]
        assert openai["input"][1:] == anthropic["messages"]
        assert openai["input"][0]["content"][0]["text"] == anthropic["system"]
        assert [(tool["name"], tool["parameters"]) for tool in openai["tools"]] == [
            (tool["name"], tool["input_schema"]) for tool in anthropic["tools"]
        ]
        assert all(tool["strict"] for tool in openai["tools"])
        with pytest.raises(ProtocolFailure, match="UNAVAILABLE_TOOL"):
            policies[0].parse(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_unavailable",
                            "name": "buy",
                            "arguments": "{}",
                        }
                    ],
                }
            )
    finally:
        for policy in policies:
            policy.client.close()


def current_action(runner):
    observation = runner.observation
    offered = set(observation.available_action_types)
    if "select_blind" in offered:
        action = {
            "type": "select_blind",
            "blind_id": observation.state.revealed_blinds[0].id,
        }
    elif "play_hand" in offered:
        action = {
            "type": "play_hand",
            "card_ids": [card.id for card in observation.state.hand],
        }
    elif "cash_out" in offered:
        action = {"type": "cash_out"}
    else:
        action = {"type": "leave_shop"}
    name = action.pop("type")
    return name, {
        **action,
        "observation_id": observation.observation_id,
        "decision_note": None,
        "note_update": {"key": "plan", "text": "MOCK_NOTE"},
    }


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_tools_inspect_calculate_correct_error_and_finish(store, monkeypatch, provider):
    config = config_for(provider)
    monkeypatch.setenv(
        "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY", "mock-only"
    )
    game = FakeGame()
    received = []
    runner = None

    def receive(request):
        body = json.loads(request.content)
        received.append(body)
        number = len(received)
        messages = body.get("input", body.get("messages"))
        if number == 1:
            name, args = "inspect_state", {"section": "public_deck_knowledge", "offset": 0}
        elif number == 2:
            assert "game_advanced" in json.dumps(messages[-1])
            name, args = "calculate", {"expression": "6*7"}
        elif number == 3:
            assert "42" in json.dumps(messages[-1])
            name, args = (
                "select_blind",
                {
                    "observation_id": 0,
                    "blind_id": "wrong",
                    "decision_note": None,
                    "note_update": None,
                },
            )
        else:
            if number == 4:
                assert "UNKNOWN_BLIND" in json.dumps(messages[-1])
                assert runner.committed == 0
            name, args = current_action(runner)
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        }
        if provider == "openai":
            response = {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"call_{number}",
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
                "usage": usage,
            }
        else:
            response = {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": f"tool_{number}", "name": name, "input": args}
                ],
                "usage": usage,
            }
        return httpx.Response(200, json=response)

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive))),
    )
    runner = Runner(store, config, game, policy)
    result = runner.run()
    assert result["outcome"] == "WIN" and result["evidence_kind"] == "SYNTHETIC_TEST"
    assert result["provider_calls"] == result["committed_actions"] + 3
    assert result["cost_usd"] == pytest.approx(len(received) * 0.000044)
    assert "MOCK_NOTE" in json.dumps(received[-1])
    if provider == "openai":
        call, output = received[1]["input"][-2:]
        assert call["type"] == "function_call" and output["type"] == "function_call_output"
        assert call["call_id"] == output["call_id"]
    else:
        call, output = received[1]["messages"][-2:]
        assert call["content"][0]["id"] == output["content"][0]["tool_use_id"]
    opened = ReviewService(store).open(result["episode_id"])
    assert "action_events" not in opened["view"]


def test_helper_budget_stops_repeated_inspection_without_advancing_game(store, monkeypatch):
    config = config_for()
    config.budgets.max_helper_calls_per_decision = 1
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    game = FakeGame()
    before = deepcopy(game.observe_private())

    def receive(_request):
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                },
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "inspect",
                        "name": "inspect_state",
                        "arguments": json.dumps({"section": "hand", "offset": 0}),
                    }
                ],
            },
        )

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive))),
    )
    result = Runner(store, config, game, policy).run()
    assert result["reason"] == "AGENT_PROTOCOL_FAILURE"
    assert result["committed_actions"] == 0 and result["provider_calls"] == 4
    assert game.observe_private() == before
    events = store.events(result["episode_id"])
    assert sum(event["type"] == "helper_result" for event in events) == 1
    assert not any(event["type"] == "action_intent" for event in events)
