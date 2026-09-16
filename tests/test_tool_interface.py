"""Named-tool integration uses mocked providers and synthetic mechanics only."""

import json
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError
from test_boundary import project

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.agents.protocol import Operation, context, decision_context, helper
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure, ProviderFailure
from balatro_horizons.agents.tool_interface import decode_tool, tools_for
from balatro_horizons.config import ROOT, ModelConfig, load_config
from balatro_horizons.contracts import RecentPublicEvent
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.review.service import ReviewService
from balatro_horizons.runner import Runner


def config_for(provider="openai"):
    config = load_config(ROOT / "configs/luna-tools-smoke.yaml")
    config.budgets.paid_calls_enabled = True
    if provider == "anthropic":
        config.models["luna"] = ModelConfig(
            provider=provider,
            model="mock-anthropic",
            input_usd_per_million=0.2,
            output_usd_per_million=1.2,
            pricing_date="2026-09-14",
            settings={"harness_interface": "tools_v2"},
        )
    return config


def test_phase_tools_and_explicit_current_blind():
    game = FakeGame()
    obs = project(game.observe_private())
    tools = {t["name"]: t for t in tools_for(obs.model_dump(mode="json"))}
    assert "operate" not in tools and "play_hand" not in tools and "buy" not in tools
    assert set(obs.available_action_types) <= tools.keys()
    assert tools["select_blind"]["parameters"]["properties"]["blind_id"]["enum"] == [
        obs.state.revealed_blinds[0].id
    ]
    for tool in tools.values():
        schema = tool["parameters"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert "$defs" not in schema and "oneOf" not in schema
    game.phase = "SELECTING_HAND"
    obs = project(game.observe_private())
    tools = {t["name"]: t for t in tools_for(obs.model_dump(mode="json"))}
    ids = [c.id for c in obs.state.hand]
    assert tools["discard"]["parameters"]["properties"]["card_ids"]["items"]["enum"] == ids
    assert tools["play_hand"]["parameters"]["properties"]["card_ids"]["items"]["enum"] == ids


def test_compact_context_defers_history_and_deck_without_destroying_them():
    obs = project(FakeGame().observe_private())
    obs.state.public_deck_knowledge.composition = {"Hearts_A": 1}
    obs.recent_public_events = [
        RecentPublicEvent(event_id=str(i), event_type="observation", summary="past public event")
        for i in range(20)
    ]
    before = obs.model_dump(mode="json")
    ctx = context(obs, interface="tools_v2")
    assert len(ctx["observation"]["recent_public_events"]) == 2
    assert ctx["omitted_event_ids"] == [str(i) for i in range(18)]
    assert "public_deck_knowledge" not in ctx["observation"]["state"]
    op = Operation.validate_python(
        {"kind": "inspect", "sections": ["public_deck_knowledge", "recent_public_events"]}
    )
    details = helper(op, [], {}, observation=obs)
    assert details["game_advanced"] is False
    assert details["sections"]["public_deck_knowledge"]["composition"] == {"Hearts_A": 1}
    assert len(details["sections"]["recent_public_events"]) == 20
    details["sections"]["recent_public_events"].clear()
    assert obs.model_dump(mode="json") == before
    for section in ("seed", "raw_engine", "checkpoint", "future"):
        with pytest.raises(ValidationError):
            Operation.validate_python({"kind": "inspect", "sections": [section]})


def test_large_repeated_inspection_delivers_each_value_once_and_fits_budget():
    cfg = config_for()
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    obs = project(game.observe_private())
    obs.state.hand[0].effects = ["RETRIEVED_CARD_DESCRIPTION" + "x" * 2000]
    obs.recent_public_events = [
        RecentPublicEvent(
            event_id=str(i), event_type="observation", summary="history " + str(i) + "y" * 200
        )
        for i in range(20)
    ]
    op = {"kind": "inspect", "sections": ["hand", "recent_public_events", "public_deck_knowledge"]}
    result = helper(Operation.validate_python(op), [], {}, obs)
    exchanges = [
        {
            "operation": op,
            "result": result,
            "tool_call": {"name": "inspect_state", "arguments": {"sections": op["sections"]}},
        }
    ] * 2
    original = deepcopy(exchanges)
    cfg.budgets.max_input_tokens_per_call = 100000
    policy = DirectProvider(cfg.models["luna"], cfg.budgets)
    old = context(obs, interface="tools_v2", byte_limit=100000)
    old_body = policy.request(old, exchanges)
    cfg.budgets.max_input_tokens_per_call = (
        len(json.dumps(old_body, ensure_ascii=False).encode()) + 4095
    )
    with pytest.raises(ProviderFailure, match="REQUIRED_CONTEXT_EXCEEDS_LIMIT"):
        policy.request(old, exchanges)
    ctx, delivered = decision_context(
        obs, exchanges, interface="tools_v2", byte_limit=cfg.budgets.max_input_tokens_per_call
    )
    body = policy.request(ctx, delivered)
    assert json.dumps(body).count("RETRIEVED_CARD_DESCRIPTION") == 1
    assert ctx["observation"]["recent_public_events"] == result["sections"]["recent_public_events"]
    assert (
        ctx["observation"]["state"]["public_deck_knowledge"]
        == result["sections"]["public_deck_knowledge"]
    )
    assert ctx["omitted_event_ids"] == []
    assert delivered[-1]["result"]["sections_read"]["hand"] == "observation.state.hand"
    assert exchanges == original
    ctx_many, many = decision_context(obs, exchanges * 4, interface="tools_v2")
    assert len(many) == 1
    assert len(ctx_many["inspection_delivery"]["coalesced_exchange_indices"]) == 7
    assert ctx_many["observation"]["state"]["hand"] == result["sections"]["hand"]


def test_named_tool_noninterference_includes_schemas_and_inspection():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    a = game.observe_private()
    for card in a["visible"]["hand"]:
        card["face_down"] = True
    b = deepcopy(a)
    for card in b["visible"]["hand"]:
        card.update(native_id="different-" + card["native_id"], label="PRIVATE_SECRET", rank="K")
    left, right = project(a), project(b)
    assert context(left, interface="tools_v2") == context(right, interface="tools_v2")
    inspect = Operation.validate_python({"kind": "inspect", "sections": ["hand"]})
    assert helper(inspect, [], {}, left) == helper(inspect, [], {}, right)
    assert "PRIVATE_SECRET" not in json.dumps(helper(inspect, [], {}, right))


def test_flat_decode_never_repairs_extra_wrappers():
    for args in ({"kind": "abort"}, {"type": "discard"}, {"envelope": {}}):
        with pytest.raises(ValueError, match="UNEXPECTED_TOOL_WRAPPER"):
            decode_tool("play_hand", args)
    op = decode_tool(
        "play_hand",
        {"observation_id": 2, "card_ids": ["a"], "decision_note": "note", "memory_update": None},
    )
    assert Operation.validate_python(op).envelope.decision_note == "note"


def test_named_provider_schema_parity_and_unavailable_tool_rejection():
    ctx = context(project(FakeGame().observe_private()), interface="tools_v2")
    policies = [
        DirectProvider(config_for(p).models["luna"], config_for(p).budgets)
        for p in ("openai", "anthropic")
    ]
    a, b = [p.request(ctx, []) for p in policies]
    assert a["input"] == b["messages"] and a["instructions"] == b["system"]
    assert [(t["name"], t["parameters"]) for t in a["tools"]] == [
        (t["name"], t["input_schema"]) for t in b["tools"]
    ]
    assert all(t["strict"] for t in a["tools"])
    assert "harness_interface" not in a and "harness_interface" not in b
    with pytest.raises(ProtocolFailure):
        policies[0].parse({"output": [{"type": "function_call", "name": "buy", "arguments": "{}"}]})


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("interface", ["tools_v2", "tools_v3", "tools_v4"])
def test_named_tools_inspect_calculate_correct_error_and_finish(
    store, monkeypatch, provider, interface
):
    config = config_for(provider)
    config.models["luna"].settings["harness_interface"] = interface
    if interface == "tools_v4" and provider == "openai":
        config.models["luna"].cached_input_usd_per_million = 0.02
        config.models["luna"].cache_write_input_usd_per_million = 0.25
    monkeypatch.setenv(
        "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY", "mock-only"
    )
    game = FakeGame()
    received = []
    baseline = Baseline("heuristic")
    runner = None

    def receive(request):
        body = json.loads(request.content)
        received.append(body)
        n = len(received)
        messages = body.get("input", body.get("messages"))
        if n == 1:
            name, args = (
                "inspect_state",
                {"sections": ["public_deck_knowledge", "recent_public_events"]}
                if interface == "tools_v2"
                else {"section": "public_deck_knowledge", "offset": 0},
            )
        elif n == 2:
            assert "game_advanced" in json.dumps(messages[-1])
            name, args = "calculate", {"expression": "6*7"}
        elif n == 3:
            assert "42" in json.dumps(messages[-1])
            name, args = (
                "select_blind",
                {
                    "observation_id": 0,
                    "blind_id": "wrong",
                    "memory_update": None,
                    "decision_note": None,
                },
            )
        else:
            if n == 4:
                assert "UNKNOWN_BLIND" in json.dumps(messages[-1])
                assert "current_blind_id" in json.dumps(messages[-1])
                assert runner.committed == 0
            operation = baseline.decide(context(runner.observation), [])
            envelope = operation["envelope"]
            action = dict(envelope["action"])
            name = action.pop("type")
            args = {
                **action,
                "observation_id": envelope["observation_id"],
                "memory_update": "V2_MOCK_MEMORY",
                "decision_note": None,
            }
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        }
        if provider == "openai":
            response = {
                "status": "completed",
                "output": [{"type": "function_call", "name": name, "arguments": json.dumps(args)}],
                "usage": usage,
            }
        else:
            response = {
                "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "name": name, "input": args}],
                "usage": usage,
            }
        return httpx.Response(200, json=response)

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    runner = Runner(store, config, game, policy)
    result = runner.run()
    assert result["outcome"] == "WIN" and result["evidence_kind"] == "SYNTHETIC_TEST"
    assert result["provider_calls"] == result["committed_actions"] + 3
    assert result["cost_usd"] == pytest.approx(len(received) * 0.000044)
    assert "V2_MOCK_MEMORY" in json.dumps(received[-1])
    if provider == "openai":
        call, output = received[1]["input"][-2:]
        assert call["type"] == "function_call" and output["type"] == "function_call_output"
        assert call["call_id"] == output["call_id"]
    else:
        call, output = received[1]["messages"][-2:]
        assert call["content"][0]["id"] == output["content"][0]["tool_use_id"]
    review = ReviewService(store)
    opened = review.open(result["episode_id"])
    assert "action_events" not in opened["view"]
    revealed = review.advance(opened["review_token"])
    assert any(e["type"] == "helper_result" for e in revealed["action_events"])


def test_inspection_respects_helper_budget_without_advancing_game(store, monkeypatch):
    config = config_for()
    config.budgets.max_helper_calls_per_decision = 1
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    game = FakeGame()
    before = game.observe_private()

    def receive(request):
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "output": [
                    {
                        "type": "function_call",
                        "name": "inspect_state",
                        "arguments": json.dumps({"sections": ["hand"]}),
                    }
                ],
            },
        )

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    result = Runner(store, config, game, policy).run()
    assert result["reason"] == "HELPER_CALL_LIMIT"
    assert result["committed_actions"] == 0 and result["provider_calls"] == 2
    assert game.observe_private() == before
    events = store.events(result["episode_id"])
    assert sum(e["type"] == "helper_result" for e in events) == 1
    assert not any(e["type"] == "action_intent" for e in events)
