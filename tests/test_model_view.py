"""V8 removes duplicate facts, not evidence, types, order, or current legality."""

import json
from copy import deepcopy

import pytest
from test_boundary import project
from test_model_reference_delivery import pages
from test_public_information import native_state, request

from balatro_horizons.contracts import Observation
from balatro_horizons.game.state import normalize
from balatro_horizons.harness.context.build import context, context_bound, working_context
from balatro_horizons.harness.context.model_view import LAST_ACTION_REF, compact_context
from balatro_horizons.harness.context.references import ModelReferences
from balatro_horizons.harness.transport import context_payload


def exact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sample():
    receipt = {"source": "observed_public_states", "hand_score": None,
               "changes": [{"path": ["money"], "before": "0", "after": "5"}],
               "selected_objects": [{"id": "card", "label": "Before mutation"}]}
    offer = {"id": "offer", "label": "Joker", "kind": "joker", "price": "2",
             "effects": ["eternal: True", "rental: True"], "counters": {}, "face_down": False}
    quote = {"offer_id": offer["id"], "label": offer["label"], "kind": offer["kind"],
             "effects": deepcopy(offer["effects"]), "cash_cost": offer["price"],
             "affordable": True, "operation": "buy", "purchase_modes": {
                 "acquire": {"status": "legal"},
                 "buy_and_use": {"status": "unavailable", "reason": "INVALID_MODE"}},
             "target_count": {"min": 0, "max": 0}, "recorded_obligations": [
                 {"kind": "rental", "amount": None, "timing": None, "source": "public_rental_flag"}]}
    return {
        "observation": {"observation_id": 4, "state": {"offers": [offer]}, "last_action": receipt},
        "current_costs": {"observation_id": 4, "cash_balance": "20", "offers": [quote]},
        "working_memory": {"frames": [{"decision_id": 3, "observed_result": deepcopy(receipt)}]},
        "run_notebook": {"entries": {"plan": "Keep  two spaces and stale claims verbatim"}},
        "previous_action_outcome": {"status": "committed", "recorded_note_update": None},
    }


def expand_sample(value):
    """Independent inverse for this fixture; never call production compaction."""
    result = deepcopy(value)
    quotes = []
    for offer in result["observation"]["state"]["offers"]:
        quote = offer.pop("quote")
        quote["offer_id"] = offer["id"]
        for key in ("label", "kind", "effects"):
            quote.setdefault(key, deepcopy(offer[key]))
        offer.setdefault("price", quote["cash_cost"])
        quotes.append(quote)
    result["current_costs"]["offers"] = quotes
    for frame in result["working_memory"]["frames"]:
        if frame.get("observed_result_ref") == LAST_ACTION_REF:
            del frame["observed_result_ref"]
            frame["observed_result"] = deepcopy(result["observation"]["last_action"])
    return result


@pytest.mark.parametrize("price", [None, "0", "2", "-1"])
def test_compact_facts_round_trip_without_mutating_canonical_context(price):
    original = sample()
    original["observation"]["state"]["offers"][0]["price"] = price
    original["current_costs"]["offers"][0]["cash_cost"] = price
    before = exact(original)
    compact = compact_context(original)
    offer = compact["observation"]["state"]["offers"][0]
    assert "offers" not in compact["current_costs"] and "price" not in offer
    assert not {"offer_id", "label", "kind", "effects"} & offer["quote"].keys()
    assert offer["quote"]["cash_cost"] == price
    assert compact["working_memory"]["frames"][0]["observed_result_ref"] == LAST_ACTION_REF
    assert exact(expand_sample(compact)) == before
    assert exact(original) == before
    assert len(exact(compact)) < len(before)
    assert compact_context(compact) == compact  # Repeated provider rendering is stable.


@pytest.mark.parametrize("field,value", [("price", 0), ("price", False), ("price", None),
                                        ("label", "Different quote label"), ("effects", [])])
def test_unequal_fields_are_retained_instead_of_silently_reconciled(field, value):
    original = sample()
    original["observation"]["state"]["offers"][0][field] = value
    compact = compact_context(original)
    offer = compact["observation"]["state"]["offers"][0]
    assert exact(offer[field]) == exact(value)
    assert exact(expand_sample(compact)) == exact(original)


@pytest.mark.parametrize("different", [None, False, 0, "0", [], {}, {"extra": None}])
def test_receipt_dedup_does_not_conflate_missing_null_empty_or_types(different):
    original = sample()
    original["observation"]["last_action"]["typed"] = False
    frame = original["working_memory"]["frames"][0]
    frame["observed_result"]["typed"] = different
    compact = compact_context(original)
    delivered = compact["working_memory"]["frames"][0]
    if different is False:
        assert delivered["observed_result_ref"] == LAST_ACTION_REF
    else:
        assert "observed_result_ref" not in delivered
        assert exact(delivered["observed_result"]) == exact(frame["observed_result"])


def test_missing_receipts_and_distinct_audit_identity_are_not_deduplicated():
    original = sample()
    original["observation"]["last_action"]["event_id"] = "latest"
    original["working_memory"]["frames"][0]["observed_result"]["event_id"] = "older"
    original["working_memory"]["frames"].extend([{"decision_id": 1}, {"observed_result": None}])
    view = ModelReferences().project(compact_context(original))
    assert "observed_result_ref" not in exact(view)
    assert view["working_memory"]["frames"][1:] == [{"decision_id": 1}, {"observed_result": None}]


def test_quotes_join_by_identity_without_reordering_or_reusing_stale_quotes():
    original = sample()
    offers, quotes = original["observation"]["state"]["offers"], original["current_costs"]["offers"]
    offers.append({**deepcopy(offers[0]), "id": "second", "price": "6"})
    quotes.insert(0, {**deepcopy(quotes[0]), "offer_id": "second", "cash_cost": "6"})
    view = compact_context(original)
    assert [(o["id"], o["quote"]["cash_cost"]) for o in view["observation"]["state"]["offers"]] == [
        ("offer", "2"), ("second", "6")]
    original["current_costs"]["observation_id"] -= 1
    assert compact_context(original)["current_costs"] == original["current_costs"]
    assert all("quote" not in o for o in compact_context(original)["observation"]["state"]["offers"])


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("phase", ["SHOP", "STANDARD_PACK"])
def test_final_provider_quote_keeps_eligibility_targets_rental_and_unknowns(provider, phase):
    obs = project(normalize(native_state(phase)))
    obs.state.offers[0].price = None
    obs.state.offers[0].effects += ["eternal: True", "rental: True"]
    obs.state.offers[0].min_targets = obs.state.offers[0].max_targets = 1
    obs.state.resources.joker_capacity = 0
    ctx = context(obs)
    canonical = deepcopy(dict(ctx))
    body = context_payload(ctx, [], provider)
    messages = body["input" if provider == "openai" else "messages"]
    view = json.loads(next(m for m in messages if m.get("role") == "user")["content"])
    offer = view["observation"]["state"]["offers"][0]
    quote = offer["quote"]
    expected = ctx.current_costs["offers"][0]
    for key in ("cash_cost", "affordable", "operation", "purchase_modes", "target_count", "recorded_obligations"):
        assert exact(quote[key]) == exact(expected[key])
    assert quote["cash_cost"] is None and quote["target_count"] == {"min": 1, "max": 1}
    assert quote["recorded_obligations"][0]["amount"] is None
    assert offer["effects"][-2:] == ["eternal: True", "rental: True"]
    assert dict(ctx) == canonical


def test_standalone_history_receipt_pages_remain_complete_and_self_contained(store, episode):
    events = store.events(episode)
    observation = next(e for e in events if e["type"] == "observation" and e["observation_id"] == 1)
    obs = Observation.model_validate(observation["payload"])
    refs = ModelReferences()
    for event in events:
        refs.consume(event)
        if event is observation:
            break
    receipt, _, _ = pages({"kind": "action_result", "decision_id": 0}, events, obs, refs)
    assert receipt["observed_result"] == refs.project(obs.last_action.model_dump(mode="json"))
    assert "observed_result_ref" not in exact(receipt)
    inspected, _, _ = pages({"kind": "inspect_page", "section": "last_action"}, events, obs, refs)
    assert inspected == receipt["observed_result"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_quote_refresh_after_pack_return_preserves_catalog_and_static_prompt(provider):
    pack = project(normalize(native_state("STANDARD_PACK")))
    shop = project(normalize(native_state("SHOP")))
    shop.observation_id += 1
    pack_body, shop_body = request(pack, provider), request(shop, provider)
    assert pack_body["tools"] == shop_body["tools"]
    key = "input" if provider == "openai" else "messages"
    views = [json.loads(next(m for m in body[key] if m.get("role") == "user")["content"])
             for body in (pack_body, shop_body)]
    assert [v["observation"]["state"]["offers"][0]["quote"]["cash_cost"] for v in views] == ["0", "7"]
    if provider == "openai":
        assert pack_body["input"][0] == shop_body["input"][0]
    else:
        assert pack_body["system"] == shop_body["system"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_request_receipt_reference_survives_followup_and_cannot_dangle_after_pruning(provider):
    ctx = context(project(normalize(native_state())))
    receipt = sample()["observation"]["last_action"]
    ctx.observation["last_action"] = receipt
    ctx.working_memory["frames"] = [{"observed_result": deepcopy(receipt)}]
    exchange = {"operation": {"kind": "arithmetic", "expression": "1+1"},
                "tool_call": {"name": "calculate", "arguments": {"expression": "1+1"}},
                "result": {"observed_result": deepcopy(receipt), "game_advanced": False}}
    for exchanges in ([], [exchange]):
        body = context_payload(ctx, exchanges, provider)
        messages = body["input" if provider == "openai" else "messages"]
        view = json.loads(next(m for m in messages if m.get("role") == "user")["content"])
        assert view["working_memory"]["frames"] == [{"observed_result_ref": LAST_ACTION_REF}]
        assert view["observation"]["last_action"] == ctx.model_references.project(receipt)
        # Helper evidence is never changed into a cross-message reference.
        delivered = [message for message in messages if message.get("role") != "developer"]
        assert json.dumps(delivered).count("observed_result_ref") == 1
    ctx, _ = working_context(ctx, [], context_bound(ctx, []) - 1)
    body = context_payload(ctx, [], provider)
    messages = body["input" if provider == "openai" else "messages"]
    view = json.loads(next(m for m in messages if m.get("role") == "user")["content"])
    assert not view["working_memory"]["frames"]
    assert "observed_result_ref" not in json.dumps(view)
    assert view["observation"]["last_action"] == ctx.model_references.project(receipt)
