"""Public context compression, bounded retrieval and provider parity; no live APIs."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_boundary import project
from test_harness_tools import config_for

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.agents.budget import reservation_usd
from balatro_horizons.agents.focused import (
    CARD_DEFAULTS,
    COUNTER_DEFAULTS,
    PAGE_BYTES,
    encode,
    text_page,
)
from balatro_horizons.agents.notebook import RunNotebook
from balatro_horizons.agents.protocol import Operation, context, decision_context, helper
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure, ProviderFailure
from balatro_horizons.agents.skills import load_guide
from balatro_horizons.config import (
    CONTEXT_FRAMING_BYTES,
    CONTEXT_SETTINGS_BYTES,
    Limits,
    ModelConfig,
)
from balatro_horizons.contracts import Observation
from balatro_horizons.game.fake import FakeGame


def read(raw, obs, events=(), rules=None):
    return helper(Operation.validate_python(raw), events, rules or {}, obs)


def cache_model():
    return ModelConfig(
        provider="openai",
        model="gpt-5.6-terra",
        input_usd_per_million=2,
        output_usd_per_million=12,
        cached_input_usd_per_million=0.2,
        cache_write_input_usd_per_million=2.5,
        pricing_date="2026-09-15",
        settings={},
    )


def cache_request(observation, exchanges=(), *, notebook=None):
    ctx, delivered = decision_context(
        observation,
        exchanges,
        skills=load_guide()[1],
        notebook=notebook,
    )
    policy = DirectProvider(cache_model(), Limits())
    try:
        return policy.request(ctx, delivered)
    finally:
        policy.client.close()


def test_cache_prefix_stays_identical_across_phases_ids_and_helper_calls():
    game = FakeGame()
    first = cache_request(project(game.observe_private()))
    game.phase = "SELECTING_HAND"
    other = project(game.observe_private())
    other.observation_id = 19
    for card in other.state.hand:
        card.id = "new-" + card.id
    book = RunNotebook()
    change, _ = book.propose("set_run_note", "plan", "changing public memory")
    book.apply(change)
    second = cache_request(other, notebook=book.view())
    def prefix(body):
        return body["tools"], body["input"][0]

    assert prefix(first) == prefix(second)
    assert first["tool_choice"] == second["tool_choice"] == "auto"
    assert "observation_id" in first["input"][1]["content"]
    assert "changing public memory" in second["input"][1]["content"]
    args = {"section": "hand", "offset": 0}
    exchange = {
        "operation": {"kind": "inspect_page", **args},
        "tool_call": {"name": "inspect_state", "arguments": args},
        "result": {"content": "changing helper output", "game_advanced": False},
    }
    third = cache_request(other, [exchange], notebook=book.view())
    assert prefix(second) == prefix(third)
    assert "changing helper output" in json.dumps(third["input"][2:])
    assert third["prompt_cache_options"] == {"mode": "explicit"}
    assert first["input"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert "previous_response_id" not in third
    assert "instructions" not in third
    assert json.dumps(third).count('"prompt_cache_breakpoint"') == 1
    assert (
        len(json.dumps(third, ensure_ascii=False).encode())
        + CONTEXT_FRAMING_BYTES
        + CONTEXT_SETTINGS_BYTES
        <= Limits().max_request_bytes
    )


def test_static_cache_schemas_keep_current_observation_and_blind_validation():
    game = FakeGame()
    observation = project(game.observe_private())
    before = deepcopy(game.observe_private())
    ctx, delivered = decision_context(observation, [])
    policy = DirectProvider(cache_model(), Limits())
    try:
        policy.request(ctx, delivered)
        args = {
            "observation_id": observation.observation_id + 1,
            "blind_id": observation.state.revealed_blinds[0].id,
            "decision_note": None,
            "note_update": None,
        }

        def parsed():
            return Operation.validate_python(policy.parse({
                "status": "completed",
                "output": [{
                    "type": "function_call", "call_id": "cache_validation",
                    "name": "select_blind", "arguments": json.dumps(args),
                }],
            }))

        with pytest.raises(InvalidAction, match="STALE_OBSERVATION"):
            validate_action(parsed().envelope, observation)
        args["observation_id"] = observation.observation_id
        args["blind_id"] = "stale-card-handle"
        with pytest.raises(InvalidAction, match="UNKNOWN_BLIND"):
            validate_action(parsed().envelope, observation)
        assert game.observe_private() == before
    finally:
        policy.client.close()


def test_hidden_card_noninterference_includes_cache_prefix():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    left = game.observe_private()
    for card in left["visible"]["hand"]:
        card["face_down"] = True
    right = deepcopy(left)
    for card in right["visible"]["hand"]:
        card.update(native_id="hidden-" + card["native_id"], label="PRIVATE_SECRET", rank="K")
    first, second = cache_request(project(left)), cache_request(project(right))
    assert first == second
    assert "PRIVATE_SECRET" not in json.dumps(second)


def test_disjoint_cache_input_categories_and_worst_case_reservation():
    model = cache_model()
    policy = DirectProvider(model, Limits())
    try:
        assert model.maximum_input_usd_per_million == 2.5
        assert reservation_usd(model, policy.limits) == pytest.approx(
            (policy.limits.max_input_tokens_per_call * 2.5
             + policy.limits.max_output_tokens_per_call * 12) / 1_000_000
        )
        response = {
            "usage": {
                "input_tokens": 5000,
                "output_tokens": 100,
                "input_tokens_details": {"cached_tokens": 3000, "cache_write_tokens": 1000},
            }
        }
        assert policy.usage_cost(response, 0.2) == pytest.approx(
            (1000 * 2 + 3000 * 0.2 + 1000 * 2.5 + 100 * 12) / 1_000_000
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
    finally:
        policy.client.close()


@pytest.mark.parametrize("model_changes,limits,error", [
    ({"cached_input_usd_per_million": None, "cache_write_input_usd_per_million": None},
     Limits(), "EXPLICIT_CACHE_PRICING_REQUIRED"),
    ({}, Limits(max_input_tokens_per_call=272_001), "CACHE_LONG_CONTEXT"),
])
def test_explicit_cache_requires_pricing_and_supported_context_tier(
    model_changes, limits, error
):
    observation = project(FakeGame().observe_private())
    ctx, delivered = decision_context(observation, [])
    model = ModelConfig.model_validate({**cache_model().model_dump(), **model_changes})
    policy = DirectProvider(model, limits)
    try:
        with pytest.raises(ProviderFailure, match=error):
            policy.request(ctx, delivered)
    finally:
        policy.client.close()


def test_cache_pricing_requires_both_read_and_write_rates():
    with pytest.raises(ValidationError, match="configure both cache read and write rates"):
        ModelConfig.model_validate({
            **cache_model().model_dump(), "cached_input_usd_per_million": None,
        })


def test_compression_preserves_effects_order_defaults_and_unknown_counters():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    obs = project(game.observe_private())
    obs.state.hand[0].counters = {**COUNTER_DEFAULTS, "mult": "4", "rounds_left": "0"}
    obs.state.hand[0].effects = ["Active effect"]
    original = obs.model_dump(mode="json")
    ctx = context(obs)
    cards = ctx["observation"]["state"]["hand"]
    assert [c["id"] for c in cards] == [c.id for c in obs.state.hand]
    first = cards[0]
    assert first["counter_defaults_apply"] is True
    assert first["effects"] == ["Active effect"]
    assert first["counters"] == {"mult": "4", "rounds_left": "0"}
    assert {**COUNTER_DEFAULTS, **first["counters"]} == obs.state.hand[0].counters
    for k, v in CARD_DEFAULTS.items():
        assert first.get(k, v) == original["state"]["hand"][0][k]
    assert ctx["observation"]["state"]["hand_levels"] == original["state"]["hand_levels"]
    assert obs.model_dump(mode="json") == original
    shop = obs.model_copy(update={"phase": "SHOP"})
    view = context(shop)["observation"]
    assert "hand_levels" not in view["state"]
    assert "hand_levels" in view["presentation"]["deferred_sections"]
    assert (
        json.loads(
            read({"kind": "inspect_page", "section": "hand_levels", "offset": 0}, shop)["content"]
        )
        == original["state"]["hand_levels"]
    )


def test_utf8_pages_are_bounded_lossless_and_reject_broken_cursors():
    original = {"cards": "♠🙂" * 5000}
    offset = 0
    parts = []
    while True:
        result = text_page(original, offset)
        assert len(result["content"].encode()) <= PAGE_BYTES
        parts.append(result["content"])
        if result["complete"]:
            break
        assert result["next_offset"] > offset
        offset = result["next_offset"]
    assert json.loads("".join(parts)) == original
    assert text_page("♠", 1)["error"] == "INVALID_PAGE_OFFSET"
    assert text_page("♠", 4)["error"] == "INVALID_PAGE_OFFSET"


def test_history_has_temporal_cutoff_and_complete_details_are_reloadable(store, episode):
    events = store.events(episode)
    first = next(e for e in events if e["type"] == "observation")
    obs = Observation.model_validate(first["payload"])
    page = read({"kind": "history", "offset": 0, "limit": 20}, obs, events)
    assert len(page["events"]) == 1 and page["next_offset"] is None
    assert page["events"][0]["event_id"] == first["event_id"]
    assert "memory" not in encode(page) and "GAME_WIN" not in encode(page)
    parts = []
    offset = 0
    while True:
        detail = read({"kind": "history_detail", "offset": 0, "byte_offset": offset}, obs, events)
        parts.append(detail["content"])
        if detail["complete"]:
            break
        offset = detail["next_offset"]
    assert json.loads("".join(parts)) == first
    assert (
        read({"kind": "history_detail", "offset": 1, "byte_offset": 0}, obs, events)["error"]
        == "UNKNOWN_PUBLIC_EVENT"
    )
    assert (
        read({"kind": "history", "offset": 0, "limit": 20}, obs, events[events.index(first) + 1 :])[
            "events"
        ]
        == []
    )


def test_native_rules_and_index_are_paged_without_mutation():
    obs = project(FakeGame().observe_private())
    rules = {"entries": {"Large rule": "♠" * 10000}, "aliases": {"large rule": "Large rule"}}
    before = deepcopy(rules)
    key = "large rule"
    parts = []
    while key:
        result = read({"kind": "rules", "key": key}, obs, rules=rules)
        parts.append(result["content"])
        assert len(result["content"].encode()) <= PAGE_BYTES
        key = result["next_key"]
    assert "".join(parts) == rules["entries"]["Large rule"]
    assert rules == before


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_bounded_working_set_clears_with_receipts_and_preserves_source(provider):
    obs = project(FakeGame().observe_private())
    cfg = config_for(provider)
    skills = load_guide()[1]
    exchanges = []
    for i in range(8):
        args = {"section": "public_deck_knowledge", "offset": i * PAGE_BYTES}
        exchanges.append(
            {
                "operation": {"kind": "inspect_page", **args},
                "tool_call": {"name": "inspect_state", "arguments": args},
                "result": {"content": str(i) + "x" * 2000, "next_offset": (i + 1) * PAGE_BYTES},
            }
        )
    before = deepcopy(exchanges)
    ctx, delivered = decision_context(obs, exchanges, skills=skills)
    assert len(delivered) == 3
    assert [
        r["exchange_index"] for r in ctx["observation"]["retrieval_context"]["cleared"]
    ] == list(range(5))
    assert ctx["observation"]["retrieval_context"]["loaded_exchange_indices"] == [5, 6, 7]
    with DirectProvider(cfg.models["luna"], cfg.budgets).client as client:
        body = DirectProvider(cfg.models["luna"], cfg.budgets, client).request(ctx, delivered)
    assert (
        len(json.dumps(body, ensure_ascii=False).encode()) + 4096
        <= cfg.budgets.max_request_bytes
    )
    initial, _ = decision_context(obs, [], skills=skills)
    byte_limit = initial["context_bytes_upper_bound"] + 1500
    smaller, retained = decision_context(
        obs, exchanges, skills=skills, byte_limit=byte_limit
    )
    assert len(retained) < 3
    cfg.budgets.max_request_bytes = byte_limit
    policy = DirectProvider(cfg.models["luna"], cfg.budgets)
    try:
        policy.request(smaller, retained)
    finally:
        policy.client.close()
    assert exchanges == before


def test_hidden_state_noninterference_and_tool_schema_parity():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    left = game.observe_private()
    for card in left["visible"]["hand"]:
        card["face_down"] = True
    right = deepcopy(left)
    for card in right["visible"]["hand"]:
        card.update(native_id="private-new-id", label="PRIVATE_SENTINEL", rank="A")
    a, b = project(left), project(right)
    assert context(a) == context(b)
    for card in context(a)["observation"]["state"]["hand"]:
        assert "counter_defaults_apply" not in card
    op = {"kind": "inspect_page", "section": "hand", "offset": 0}
    assert read(op, a) == read(op, b)
    assert "PRIVATE_SENTINEL" not in encode(read(op, b))
    for section in ("seed", "raw_engine", "future"):
        with pytest.raises(ValidationError):
            Operation.validate_python({**op, "section": section})
    bodies = []
    for provider in ("openai", "anthropic"):
        cfg = config_for(provider)
        policy = DirectProvider(cfg.models["luna"], cfg.budgets)
        try:
            bodies.append(policy.request(context(a), []))
            with pytest.raises(ProtocolFailure, match="EXPECTED_PAGED_INSPECTION"):
                policy._decode("inspect_state", {"sections": ["hand"]})
        finally:
            policy.client.close()
    assert bodies[0]["input"][1:] == bodies[1]["messages"]
    assert bodies[0]["input"][0]["content"][0]["text"] == bodies[1]["system"]
    assert [(t["name"], t["parameters"]) for t in bodies[0]["tools"]] == [
        (t["name"], t["input_schema"]) for t in bodies[1]["tools"]
    ]
