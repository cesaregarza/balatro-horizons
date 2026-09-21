"""Single-harness named-tool integration; mocked providers and synthetic mechanics only."""

import json
from copy import deepcopy

import httpx
import pytest
from provider_transport import with_input_count
from pydantic import ValidationError
from test_boundary import project

from balatro_horizons.actions.validation import validate_action
from balatro_horizons.agents.baselines import Baseline, baseline_observation, candidates
from balatro_horizons.agents.input_limits import request_size
from balatro_horizons.agents.tool_interface import ACTION_MODELS, decode_tool
from balatro_horizons.config import ROOT, ModelConfig, load_config
from balatro_horizons.contracts import ActionEnvelope, RecentPublicEvent
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context, decision_context
from balatro_horizons.harness.context.present import PAGE_BYTES
from balatro_horizons.harness.contract import Operation
from balatro_horizons.harness.helpers import helper
from balatro_horizons.harness.transport import DirectProvider, ProtocolFailure
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
    assert (
        blind["observation"]["current_blind_id"]
        == project(game.observe_private()).state.revealed_blinds[0].id
    )
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
    assert "current_blind_id" not in hand["observation"]


def test_compact_context_defers_public_history_and_deck_without_mutating_source():
    observation = project(FakeGame().observe_private())
    observation.state.public_deck_knowledge.composition = {"Hearts_A": 1}
    observation.recent_public_events = [
        RecentPublicEvent(event_id=str(i), event_type="observation", summary="past public event")
        for i in range(20)
    ]
    before = observation.model_dump(mode="json")
    compact = context(observation)
    assert [event["event_id"] for event in compact["observation"]["recent_public_events"]] == [
        "18",
        "19",
    ]
    assert compact["omitted_event_ids"] == [str(i) for i in range(18)]
    assert "public_deck_knowledge" not in compact["observation"]["state"]
    for section, expected in (
        ("public_deck_knowledge", before["state"]["public_deck_knowledge"]),
        ("recent_public_events", before["recent_public_events"]),
    ):
        page = helper(
            Operation.validate_python({"kind": "inspect_page", "section": section, "offset": 0}),
            [],
            {},
            observation,
        )
        assert page["game_advanced"] is False and page["complete"] is True
        assert json.loads(page["content"]) == expected
    assert observation.model_dump(mode="json") == before
    for section in ("seed", "raw_engine", "checkpoint", "future"):
        with pytest.raises(ValidationError):
            Operation.validate_python({"kind": "inspect_page", "section": section, "offset": 0})


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_repeated_large_inspection_is_paged_retained_and_within_request_bytes(provider):
    observation = project(FakeGame().observe_private())
    observation.recent_public_events = [
        RecentPublicEvent(
            event_id=str(i), event_type="observation", summary=f"RETRIEVED_EVENT_{i}_" + "x" * 220
        )
        for i in range(20)
    ]
    operation = {"kind": "inspect_page", "section": "recent_public_events", "offset": 0}
    page = helper(Operation.validate_python(operation), [], {}, observation)
    assert page["total_bytes"] > PAGE_BYTES
    assert len(page["content"].encode()) <= PAGE_BYTES
    assert page["game_advanced"] is False
    exchange = {
        "operation": operation,
        "tool_call": {
            "name": "inspect_state",
            "arguments": {"section": "recent_public_events", "offset": 0},
        },
        "result": page,
    }
    exchanges = [deepcopy(exchange) for _ in range(8)]
    original = deepcopy(exchanges)
    initial, _ = decision_context(observation, [])
    byte_limit = initial["context_bytes_upper_bound"] + 6000
    compact, delivered = decision_context(observation, exchanges, byte_limit=byte_limit)
    assert len(delivered) == 1
    assert compact["observation"]["retrieval_context"]["loaded_exchange_indices"] == [7]
    assert [row["exchange_index"] for row in compact["context_delivery"]["cleared"]] == list(
        range(7)
    )
    assert "RETRIEVED_EVENT_0_" not in json.dumps(compact["observation"])
    assert delivered[0]["result"]["content"] == page["content"]
    assert exchanges == original
    config = config_for(provider)
    config.budgets.max_request_bytes = byte_limit
    policy = DirectProvider(config.models["luna"], config.budgets)
    try:
        body = policy.request(compact, delivered)
        assert request_size(body) <= byte_limit
    finally:
        policy.client.close()


def test_named_tool_schema_and_inspection_ignore_hidden_card_identity():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    private = game.observe_private()
    for card in private["visible"]["hand"]:
        card["face_down"] = True
    changed = deepcopy(private)
    for card in changed["visible"]["hand"]:
        card.update(native_id="hidden-other-id", label="PRIVATE_SENTINEL", rank="K")
    left, right = project(private), project(changed)
    first, second = context(left), context(right)
    assert first == second
    inspection = Operation.validate_python({"kind": "inspect_page", "section": "hand", "offset": 0})
    assert helper(inspection, [], {}, left) == helper(inspection, [], {}, right)
    assert "PRIVATE_SENTINEL" not in json.dumps(helper(inspection, [], {}, right))
    for provider in ("openai", "anthropic"):
        config = config_for(provider)
        policy = DirectProvider(config.models["luna"], config.budgets)
        try:
            left_body, right_body = policy.request(first, []), policy.request(second, [])
            assert left_body["tools"] == right_body["tools"]
            assert left_body == right_body
        finally:
            policy.client.close()


def test_baselines_keep_canonical_action_validation_in_compact_shop_view():
    game = FakeGame()
    game.phase = "SHOP"
    observation = project(game.observe_private())
    delivered = context(observation)
    choices = [
        candidate.model_dump(mode="json")
        for candidate in candidates(baseline_observation(delivered["observation"]))
    ]
    assert choices == [candidate.model_dump(mode="json") for candidate in candidates(observation)]
    assert {choice["action"]["type"] for choice in choices} == {"buy", "leave_shop"}
    assert all(choice["action"].get("mode") != "buy_and_use" for choice in choices)
    for seed in range(20):
        operation = Baseline("random_legal", seed).decide(delivered, [])
        validate_action(ActionEnvelope.model_validate(operation["envelope"]), observation)
    operation = Baseline("heuristic").decide(delivered, [])
    validate_action(ActionEnvelope.model_validate(operation["envelope"]), observation)


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


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_oversized_named_note_write_exhausts_helpers_then_allows_model_game_action(
    store, monkeypatch, provider
):
    config = config_for(provider)
    config.budgets.max_helper_calls_per_decision = 1
    monkeypatch.setenv(
        "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY", "mock-only"
    )
    game = FakeGame()
    before = deepcopy(game.observe_private())
    calls = []
    runner = None

    def receive(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            name, args = "set_run_note", {"key": "plan", "text": "x" * 4096}
        elif len(calls) == 2:
            assert "RUN_NOTEBOOK_LIMIT" in json.dumps(body.get("input", body.get("messages"))[-1])
            name, args = "set_run_note", {"key": "plan", "text": "retry"}
        elif len(calls) == 3:
            assert "HELPER_LIMIT_REACHED" in json.dumps(body.get("input", body.get("messages"))[-1])
            name, args = (
                "select_blind",
                {
                    "observation_id": runner.observation.observation_id,
                    "blind_id": runner.observation.state.revealed_blinds[0].id,
                    "decision_note": None,
                    "note_update": None,
                },
            )
        else:
            name, args = "abort_run", {"reason": "capacity checked"}
        usage = {"input_tokens": 100, "output_tokens": 20}
        if provider == "openai":
            response = {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"call_{len(calls)}",
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
                    {"type": "tool_use", "id": f"tool_{len(calls)}", "name": name, "input": args}
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
    assert result["reason"] == "AGENT_ABORT"
    assert result["committed_actions"] == 1 and result["provider_calls"] == 4
    assert game.observe_private() != before
    events = store.events(result["episode_id"])
    feedback = [event["payload"]["result"] for event in events if event["type"] == "helper_result"]
    assert len(feedback) == 1
    assert feedback[0]["error"] == "RUN_NOTEBOOK_LIMIT"
    assert feedback[0]["game_advanced"] is False
    assert len(json.dumps(feedback[0])) < 512
    contexts = [event["payload"]["context"] for event in events if event["type"] == "agent_context"]
    assert "set_run_note" in contexts[0]["allowed_tools"]
    assert "set_run_note" not in contexts[1]["allowed_tools"]
    assert "set_run_note" not in contexts[2]["allowed_tools"]
    rejected = [event["payload"] for event in events if event["type"] == "action_rejected"]
    assert [row["code"] for row in rejected] == ["HELPER_LIMIT_REACHED"]
    assert rejected[0]["feedback"]["helper_calls_remaining"] == 0
    committed = [event["payload"] for event in events if event["type"] == "action_commit"]
    assert len(committed) == 1 and committed[0]["action"]["type"] == "select_blind"
    assert committed[0]["action"]["blind_id"] == contexts[2]["observation"]["current_blind_id"]
    submitted = [
        event["payload"]["operation"]["envelope"]["action"]
        for event in events
        if event["type"] == "agent_operation" and event["payload"]["operation"]["kind"] == "action"
    ]
    intended = [event["payload"]["action"] for event in events if event["type"] == "action_intent"]
    assert submitted == intended == [committed[0]["action"]]
    assert not any(event["type"] == "run_note" for event in events)
