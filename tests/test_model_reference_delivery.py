"""Exercise integer references across provider, paging, and history boundaries."""

import json
import re
from copy import deepcopy

import pytest
import test_campaign_budget
from test_boundary import project
from test_budget_continuation import stopped
from test_harness_context import cache_model
from test_provider_continuations import model
from test_restore_unfinished import finish

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.config import Limits
from balatro_horizons.contracts import Observation
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.context.present import public_history
from balatro_horizons.harness.context.references import ModelReferences
from balatro_horizons.harness.contract import Operation
from balatro_horizons.harness.helpers import helper
from balatro_horizons.harness.transport import DirectProvider, ProtocolFailure

harness = test_campaign_budget.harness


def delivered_ids(events):
    result, observation = {}, None
    for event in events:
        if event["type"] == "observation":
            observation = event["payload"]
        if event["type"] != "provider_request":
            continue
        messages = event["payload"]["body"]["input"]
        view = json.loads(next(m for m in messages if m.get("role") == "user")["content"])
        for area in ("hand", "jokers", "consumables", "offers", "revealed_blinds"):
            original, delivered = observation["state"][area], view["observation"]["state"][area]
            assert len(original) == len(delivered)
            for canonical, compact in zip(original, delivered, strict=True):
                assert type(compact["id"]) is int and isinstance(canonical["id"], str)
                assert result.setdefault(canonical["id"], compact["id"]) == compact["id"]
    return result


def test_budget_restore_preserves_compact_ids_and_canonical_journals(harness, monkeypatch):
    observe = FakeGame.observe_private

    def persistent_object(game):
        state = observe(game)
        state["visible"]["jokers"] = [{"native_id": "persistent-fixture",
                                       "label": "Persistent fixture", "sellable": False}]
        return state

    monkeypatch.setattr(FakeGame, "observe_private", persistent_object)
    h = harness
    parent, terminal, _, _ = stopped(h)
    path = h.store.episode_path(parent) / "events.jsonl"
    original = path.read_bytes()
    parent_ids = delivered_ids(h.store.events(parent))
    service = h.service()
    child = service.continue_budget(parent, 2, expected_head=terminal["journal_head"])
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    child_ids = delivered_ids(h.store.events(child))
    shared = parent_ids.keys() & child_ids.keys()
    assert shared and all(parent_ids[key] == child_ids[key] for key in shared)
    assert path.read_bytes() == original
    assert len(set(child_ids.values())) == len(child_ids)
    h.native.assert_not_called()


def response(provider, name, arguments):
    if provider == "openai":
        return {"status": "completed", "output": [{"type": "function_call", "call_id": "call_x",
                "name": name, "arguments": json.dumps(arguments)}]}
    return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "call_x",
            "name": name, "input": arguments}]}


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_wire_purchase_decodes_to_canonical_id_and_preserves_provider_blocks(provider):
    game = FakeGame()
    game.phase = "SHOP"
    obs = project(game.observe_private())
    ctx = context(obs)
    policy = DirectProvider(model(provider), Limits())
    try:
        body = policy.request(ctx, [])
        messages = body.get("input", body.get("messages"))
        view = json.loads(next(m for m in messages if m.get("role") == "user")["content"])
        offer = view["observation"]["state"]["offers"][0]
        arguments = {"observation_id": obs.observation_id, "offer_id": offer["id"],
                     "mode": "acquire", "target_ids": [], "decision_note": "unchanged",
                     "note_update": {"key": "plan", "text": "literal note"}}
        returned = response(provider, "buy", arguments)
        original = deepcopy(returned)
        operation = Operation.validate_python(policy.parse(returned))
        assert operation.envelope.action.offer_id == obs.state.offers[0].id
        assert policy.last_tool_call["arguments"] == arguments
        assert policy.last_provider_turn["items"] == original.get("output", original.get("content"))
        assert returned == original
        validate_action(operation.envelope, obs)
        arguments["observation_id"] += 1
        stale = Operation.validate_python(policy.parse(response(provider, "buy", arguments)))
        with pytest.raises(InvalidAction, match="STALE_OBSERVATION"):
            validate_action(stale.envelope, obs)
        arguments["offer_id"] = 999999
        with pytest.raises(ProtocolFailure, match="UNKNOWN_MODEL_REFERENCE"):
            policy.parse(response(provider, "buy", arguments))
    finally:
        policy.client.close()


def pages(raw, events, obs, refs):
    cursor = "offset" if raw["kind"] == "inspect_page" else "byte_offset"
    output = []
    while True:
        page = helper(Operation.validate_python(raw), events, {}, obs, references=refs)
        assert page["game_advanced"] is False and "error" not in page
        output.append(page["content"])
        if page["next_offset"] is None:
            assert sum(len(item.encode()) for item in output) == page["total_bytes"]
            return json.loads("".join(output)), len(output), page
        assert page["next_offset"] > raw.get(cursor, 0)
        raw = {**raw, cursor: page["next_offset"]}


def test_inspection_translates_before_utf8_pagination_not_after():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    obs = project(game.observe_private())
    obs.state.hand[0].label = "猫" * 2000
    refs = ModelReferences()
    refs.observe(obs.model_dump(mode="json"))
    value, count, _ = pages({"kind": "inspect_page", "section": "hand", "offset": 0}, [], obs, refs)
    assert count > 2
    assert value == refs.project([card.model_dump(mode="json") for card in obs.state.hand])
    assert not re.search(r"h_[0-9a-f]{20}", json.dumps(value))
    assert value[0]["label"] == obs.state.hand[0].label


def test_history_and_action_receipts_translate_before_paging_with_safe_cutoff(store, episode):
    events = store.events(episode)
    obs = Observation.model_validate(next(e["payload"] for e in events
        if e["type"] == "observation" and e["observation_id"] == 1))
    history = public_history(events, obs)
    refs = ModelReferences()
    for event in history:
        refs.consume(event)
    first, _, _ = pages({"kind": "history_detail", "offset": 0}, events, obs, refs)
    assert first == refs.project(history[0])
    assert not any(key in first for key in ("hash", "previous_hash", "event_id", "request_id"))
    arguments = refs.arguments({"episode_id": refs.episodes[episode], "decision_id": 0})
    before, _, page = pages({"kind": "action_result", "section": "before", **arguments},
                             events, obs, refs)
    assert before == refs.project(history[0]["payload"])
    assert page["references"]["action"]["episode_id"] == refs.episodes[episode]
    assert "event_id" not in page["references"]["action"]
    future = helper(Operation.validate_python({"kind": "action_result", "decision_id": 1}),
                    events, {}, obs, references=refs)
    assert future["error"] == "ACTION_RESULT_NOT_AVAILABLE"


def test_a_removed_reference_cannot_target_a_new_object_in_its_old_position():
    game = FakeGame()
    game.phase = "SHOP"
    old = project(game.observe_private())
    ctx = context(old)
    removed_id = old.state.offers[0].id
    number = ctx.model_references.objects[removed_id]
    new = old.model_copy(deep=True)
    new.observation_id += 1
    new.state.offers[0].id = "h_" + "f" * 20
    ctx = context(new, references=ctx.model_references)
    policy = DirectProvider(cache_model(), Limits())
    try:
        policy.request(ctx, [])
        raw = policy.parse(response("openai", "buy", {"observation_id": new.observation_id,
            "offer_id": number, "mode": "acquire", "target_ids": []}))
        action = Operation.validate_python(raw).envelope
        assert action.action.offer_id == removed_id
        with pytest.raises(InvalidAction, match="UNKNOWN_OFFER"):
            validate_action(action, new)
    finally:
        policy.client.close()
