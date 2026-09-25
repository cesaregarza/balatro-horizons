import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_boundary import project
from test_public_information import delivered_observation, native_state, request

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.contracts import ActionEnvelope, Observation, PublicCard
from balatro_horizons.game.state import normalize
from balatro_horizons.observations.deltas import last_action
from balatro_horizons.observations.projection import HandleIssuer

SEQUENCES = json.loads(
    (Path(__file__).parent / "fixtures/shop-price-regressions.json").read_text()
)["sequences"]


def delivered_costs(body, provider):
    messages = body["input" if provider == "openai" else "messages"]
    return json.loads(next(m for m in messages if m.get("role") == "user")["content"])[
        "current_costs"
    ]


def native_offer(offer):
    return {
        "id": 991,
        "label": offer["name"],
        "set": offer["set"],
        "state": {"hidden": False},
        "value": {},
        "cost": {"buy": offer["price"]},
        "modifier": offer.get("modifiers", {}),
        "acquire_allowed": True,
        "usable": True,
    }


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("sequence", SEQUENCES, ids=lambda s: s["name"])
def test_recorded_shop_sequences_reach_final_provider_request(sequence, provider):
    issuer = HandleIssuer(b"s" * 32)
    previous = previous_action = first_body = None
    for step in sequence["states"]:
        raw = native_state(step["phase"])
        raw["money"], raw["round"]["reroll_cost"] = step["money"], step["reroll"]
        if sequence["chaos"]:
            raw["jokers"]["cards"] = [
                {
                    "id": 20,
                    "label": "Chaos the Clown",
                    "set": "JOKER",
                    "value": {"effect": "1 free Reroll per shop"},
                    "state": {"hidden": False},
                }
            ]
        raw["shop"]["cards"] = raw["pack"]["cards"] = []
        if step.get("offer"):
            area = (
                ("packs" if step["offer"]["set"] == "BOOSTER" else "shop")
                if step["phase"] == "SHOP"
                else "pack"
            )
            raw.setdefault(area, {})
            raw[area]["cards"] = [native_offer(step["offer"])]
        obs = project(normalize(raw), issuer, step["decision"])
        if previous_action:
            obs.last_action = last_action(previous, obs, previous_action)
        body = request(obs, provider)
        view, costs = delivered_observation(body, provider), delivered_costs(body, provider)
        assert costs["observation_id"] == view["observation_id"] == step["decision"]
        assert costs["cash_balance"] == view["state"]["resources"]["money"] == str(step["money"])
        if step["phase"] == "SHOP":
            quote = costs["rerolls"]["reroll_shop"]
            assert (
                quote["cash_cost"]
                == view["state"]["resources"]["shop_reroll_cost"]
                == str(step["reroll"])
            )
            assert quote["free_rerolls_remaining"] is None  # No guessed entitlement counter.
        else:
            assert "rerolls" not in costs
        if step.get("offer"):
            quote = view["state"]["offers"][0]["quote"]
            assert quote["cash_cost"] == str(step["offer"]["price"])
            assert "price" not in view["state"]["offers"][0] and "offers" not in costs
            if step["offer"].get("modifiers", {}).get("rental"):
                assert quote["cash_cost"] == "0" and quote["affordable"]
                assert quote["purchase_modes"]["acquire"] == {"status": "legal"}
                assert quote["recorded_obligations"] == [
                    {
                        "kind": "rental",
                        "amount": None,
                        "timing": None,
                        "source": "public_rental_flag",
                    }
                ]
        if previous_action:
            receipt = view["last_action"]["transaction"]
            assert receipt["actual_cash_charge"] is None
            assert receipt["cash_before"] == previous.state.resources.money
            assert receipt["cash_after"] == str(step["money"])
            if previous_action.type == "reroll_shop":
                assert receipt["quoted_cash_charge"] == previous.state.resources.shop_reroll_cost
        if first_body:
            assert body["tools"] == first_body["tools"]
            if provider == "openai":
                assert body["input"][0] == first_body["input"][0]
            else:
                assert body["system"] == first_body["system"]
        first_body = first_body or body
        previous = obs
        if step.get("action"):
            action = {"type": step["action"]}
            if step.get("offer"):
                action["offer_id"] = obs.state.offers[0].id
            envelope = ActionEnvelope.model_validate(
                {"observation_id": obs.observation_id, "action": action}
            )
            validate_action(envelope, obs)
            previous_action = envelope.action


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_affordability_capacity_and_targeted_modes_are_distinct(provider):
    raw = native_state()
    raw["consumables"]["limit"] = 0
    raw["shop"]["cards"] = [native_offer({"name": "The Magician", "set": "TAROT", "price": 3})]
    raw["shop"]["cards"][0].update(acquire_allowed=False, min_targets=1, max_targets=2)
    obs = project(normalize(raw))
    obs.state.hand = [PublicCard(id="target", label="5 of Clubs")]
    body = request(obs, provider)
    costs = delivered_costs(body, provider)
    offer = delivered_observation(body, provider)["state"]["offers"][0]["quote"]
    assert offer["affordable"] is True
    assert offer["purchase_modes"]["acquire"] == {
        "status": "unavailable",
        "reason": "ACQUISITION_NOT_AVAILABLE",
    }
    assert offer["purchase_modes"]["buy_and_use"] == {
        "status": "requires_targets",
        "reason": "INVALID_TARGET_COUNT",
    }
    assert costs["inventory"]["consumables"]["open_slots"] == 0
    envelope = ActionEnvelope.model_validate(
        {
            "observation_id": obs.observation_id,
            "action": {
                "type": "buy",
                "offer_id": obs.state.offers[0].id,
                "mode": "buy_and_use",
                "target_ids": ["target"],
            },
        }
    )
    validate_action(envelope, obs)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_unknown_prices_survive_projection_and_serialization_without_becoming_free(provider):
    raw = native_state()
    raw["shop"]["cards"][0]["cost"].pop("buy")
    raw["round"]["reroll_cost"] = None
    obs = project(normalize(raw))
    assert Observation.model_validate(obs.model_dump(mode="json")).state.offers[0].price is None
    body = request(obs, provider)
    costs = delivered_costs(body, provider)
    offer = delivered_observation(body, provider)["state"]["offers"][0]
    quote = offer["quote"]
    assert quote["cash_cost"] is None and quote["affordable"] is None
    assert quote["purchase_modes"]["acquire"]["status"] == "unknown"
    assert costs["rerolls"]["reroll_shop"]["cash_cost"] is None
    assert "price" not in offer and "offers" not in costs
    envelope = ActionEnvelope.model_validate(
        {
            "observation_id": obs.observation_id,
            "action": {"type": "buy", "offer_id": obs.state.offers[0].id},
        }
    )
    with pytest.raises(InvalidAction, match="VISIBLE_RESOURCE_INCONSISTENCY"):
        validate_action(envelope, obs)


@pytest.mark.parametrize(
    "money,credit,headroom,affordable",
    [("-10", "20", "10", True), ("-10", "0", "0", False), (None, "0", None, None)],
)
def test_cash_and_credit_headroom_agree_with_validator(money, credit, headroom, affordable):
    obs = project(normalize(native_state()))
    obs.state.resources.money, obs.state.resources.credit_limit = money, credit
    body = request(obs, "openai")
    costs = delivered_costs(body, "openai")
    assert costs["cash_balance"] == money and costs["positive_price_spending_headroom"] == headroom
    assert delivered_observation(body, "openai")["state"]["offers"][0]["quote"]["affordable"] is affordable


@pytest.mark.parametrize("money", ["-10", None])
def test_zero_upfront_quote_does_not_require_positive_cash_headroom(money):
    obs = project(normalize(native_state()))
    obs.state.resources.money = money
    obs.state.resources.credit_limit = "0"
    obs.state.resources.shop_reroll_cost = "0"
    obs.state.offers[0].price = "0"
    body = request(obs, "openai")
    costs = delivered_costs(body, "openai")
    quote = delivered_observation(body, "openai")["state"]["offers"][0]["quote"]
    assert quote["affordable"] is True
    assert quote["purchase_modes"]["acquire"] == {"status": "legal"}
    assert costs["rerolls"]["reroll_shop"]["affordable"] is True
    assert costs["rerolls"]["reroll_shop"]["mode_check"] == {"status": "legal"}


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_costs_do_not_reveal_concealed_offer_effects(provider):
    raw = native_state()
    raw["shop"]["cards"][0]["state"]["hidden"] = True
    changed = deepcopy(raw)
    changed["shop"]["cards"][0]["modifier"] = {"rental": True, "edition": "SECRET_EDITION"}
    assert request(project(normalize(raw)), provider) == request(
        project(normalize(changed)), provider
    )
