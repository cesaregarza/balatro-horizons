"""Compact references preserve identity and facts without exposing audit metadata."""

import json
import re
from copy import deepcopy

import pytest
from test_boundary import project

from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.context.references import ModelReferences
from balatro_horizons.harness.context.render import tool_catalog
from balatro_horizons.harness.transport.base import canonical_messages
from balatro_horizons.observations.projection import HandleIssuer


def test_model_view_has_indices_and_counts_but_canonical_context_is_unchanged():
    game = FakeGame()
    game.phase = "SHOP"
    obs = project(game.observe_private())
    ctx = context(obs)
    ctx.omitted_event_ids = ["a" * 32, "b" * 32]
    before = deepcopy(dict(ctx))
    view = json.loads(canonical_messages(ctx)[0]["content"])
    assert view["omitted_event_count"] == 2
    assert "omitted_event_ids" not in view
    assert "content_hash" not in view["run_notebook"]
    assert "model_references" not in dict(ctx)
    assert dict(ctx) == before
    assert all(type(offer["id"]) is int for offer in view["observation"]["state"]["offers"])
    assert not re.search(r"h_[0-9a-f]{20}", json.dumps(view))
    assert "content_hash" in ctx.run_notebook


def test_indices_survive_reordering_removal_and_sorted_journal_restore():
    refs = ModelReferences()
    observations = [
        {"episode_id": "a" * 32, "state": {"jokers": [{"id": "j_a"}],
            "hand": [{"id": "c_a"}, {"id": "c_b"}]}},
        {"episode_id": "a" * 32, "state": {"hand": [{"id": "c_b"}, {"id": "c_a"}],
            "jokers": [{"id": "j_a"}]}},
        {"episode_id": "b" * 32, "state": {"hand": [{"id": "c_b"}, {"id": "c_new"}]}}
    ]
    events, views = [], []
    for obs in observations:
        event = {"type": "observation", "episode_id": obs["episode_id"], "payload": obs}
        events.append(event)
        refs.consume(event)
        views.append(refs.project(obs))
    assert views[1]["state"]["hand"] == views[0]["state"]["hand"][::-1]
    assert refs.objects["c_new"] > refs.objects["c_a"]
    assert refs.arguments({"card_ids": [refs.objects["c_a"]]}) == {"card_ids": ["c_a"]}
    restored = ModelReferences()
    for event in json.loads(json.dumps(events, sort_keys=True)):
        restored.consume(event)
    assert restored.objects == refs.objects and restored.episodes == refs.episodes
    assert restored.project(observations[-1]) == views[-1]
    assert restored.arguments({"episode_id": 2}) == {"episode_id": "b" * 32}


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", "h_" + "a" * 20, None])
def test_non_integer_or_nonpositive_references_are_not_coerced(value):
    refs = ModelReferences()
    refs.project({"id": "actual"})
    with pytest.raises(ValueError, match="INVALID_MODEL_REFERENCE"):
        refs.arguments({"offer_id": value})
    with pytest.raises(ValueError, match="INVALID_MODEL_REFERENCE"):
        refs.arguments({"target_ids": [value]})


def test_unknown_reference_is_not_minted_by_model_output():
    refs = ModelReferences()
    refs.project({"id": "actual"})
    with pytest.raises(ValueError, match="UNKNOWN_MODEL_REFERENCE"):
        refs.arguments({"offer_id": 2})
    assert refs.objects == {"actual": 1}
    assert refs.arguments({"episode_id": None}) == {"episode_id": None}


def test_card_defaults_stickers_notes_and_unknown_values_are_not_reinterpreted():
    refs = ModelReferences()
    value = {"id": "h_" + "a" * 20, "face_down": True, "rank": None,
             "effects": ["eternal: True", "rental: True"], "sellable": False,
             "counters": {"hash": "game counter", "unknown": None},
             "entries": {"hash": "agent-written note", "id": "literal note"},
             "entry": {"hash": "literal rule data"}, "content_hash": "f" * 64}
    expected = {**deepcopy(value), "id": 1}
    del expected["content_hash"]
    assert refs.project(value) == expected
    assert value["id"].startswith("h_") and "content_hash" in value


def test_forced_cards_summaries_and_order_deltas_use_the_same_ids():
    first, second = "h_" + "a" * 20, "h_" + "b" * 20
    refs = ModelReferences()
    refs.observe({"state": {"hand": [{"id": first}, {"id": second}]}})
    value = {"required_ids": [first], "summary": json.dumps({"card_ids": [first]}),
             "changes": [{"path": ["hand", "order"], "before": [first, second],
                          "after": [second, first]},
                         {"path": ["hand", first, "sellable"], "before": False, "after": True}]}
    short = refs.project(value)
    assert short["required_ids"] == [1]
    assert json.loads(short["summary"])["card_ids"] == [1]
    assert short["changes"][0] == {"path": ["hand", "order"], "before": [1, 2], "after": [2, 1]}
    assert short["changes"][1]["path"] == ["hand", 1, "sellable"]
    assert "h_" not in json.dumps(short)


def test_concealment_does_not_reconnect_old_model_references():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    raw = game.observe_private()
    issuer, refs = HandleIssuer(b"a" * 32), ModelReferences()
    indices = []
    for decision, hidden in enumerate((False, True, False)):
        raw["visible"]["hand"][0]["face_down"] = hidden
        obs = project(raw, issuer, decision).model_dump(mode="json")
        refs.observe(obs)
        indices.append(refs.project(obs)["state"]["hand"][0]["id"])
    assert len(set(indices)) == 3


def test_integer_tool_schemas_do_not_repeat_shared_instructions():
    catalog = {item["name"]: item for item in tool_catalog([])}
    for name, key in (("buy", "offer_id"), ("sell", "owned_id"), ("select_blind", "blind_id")):
        props = catalog[name]["parameters"]["properties"]
        assert props[key] == {"type": "integer", "minimum": 1}
        assert "description" not in props["note_update"]
    props = catalog["play_hand"]["parameters"]["properties"]
    assert props["card_ids"]["items"]["type"] == "integer"
    assert catalog["retrieve_action_result"]["parameters"]["properties"]["episode_id"] == {
        "type": ["integer", "null"], "minimum": 1}
    for definition in catalog.values():
        targets = definition["parameters"]["properties"].get("target_ids")
        if targets is not None:
            assert "description" not in targets
