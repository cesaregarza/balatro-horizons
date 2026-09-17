import json

import pytest
from test_boundary import project
from test_public_information import delivered_observation, native_state, request

from balatro_horizons.contracts import ActionEnvelope, LastAction, Observation, PublicCard
from balatro_horizons.engine.native_state import normalize
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.observations.deltas import last_action
from balatro_horizons.review.service import ReviewService


@pytest.mark.parametrize("kind", ["buy", "choose_pack", "reroll_shop", "reroll_boss", "sell"])
def test_receipt_distinguishes_quote_from_cash_delta_and_unobserved_charge(kind):
    phase = (
        "STANDARD_PACK"
        if kind == "choose_pack"
        else "BLIND_SELECT"
        if kind == "reroll_boss"
        else "SHOP"
    )
    before = project(normalize(native_state(phase)))
    before.state.resources.money = "20"
    before.state.resources.boss_reroll_cost = "10"
    before.state.jokers = [PublicCard(id="sale", label="Joker", sell_price="4", sellable=True)]
    after = before.model_copy(deep=True)
    after.observation_id = before.observation_id + 1
    after.state.resources.money = "23"  # Other monetary effects can outweigh the quote.
    action = {"type": kind}
    if kind in ("buy", "choose_pack"):
        action["offer_id"] = before.state.offers[0].id
    elif kind == "sell":
        action["owned_id"] = "sale"
    envelope = ActionEnvelope.model_validate(
        {"observation_id": before.observation_id, "action": action}
    )
    after.last_action = last_action(before, after, envelope.action)
    receipt = after.last_action.transaction
    assert (receipt.cash_before, receipt.cash_after, receipt.net_cash_change) == ("20", "23", "3")
    assert receipt.actual_cash_charge is None and receipt.actual_cash_proceeds is None
    assert receipt.actual_charge_source == "not_observed"
    if kind == "sell":
        assert receipt.quoted_cash_charge is None and receipt.quoted_cash_proceeds == "4"
    else:
        assert (
            receipt.quoted_cash_charge
            == {"buy": "7", "choose_pack": "0", "reroll_shop": "5", "reroll_boss": "10"}[kind]
        )
        assert receipt.quoted_cash_proceeds is None
    for provider in ("openai", "anthropic"):
        delivered = delivered_observation(request(after, provider, "tools_v5"), provider)
        assert delivered["last_action"]["transaction"] == receipt.model_dump(mode="json")
    old = after.last_action.model_dump(mode="json")
    old.pop("transaction")
    assert LastAction.model_validate(old).transaction is None


def test_receipt_unknown_balances_remain_unknown():
    before = project(normalize(native_state()))
    before.state.resources.money = None
    before.state.offers[0].price = None
    after = before.model_copy(deep=True)
    after.observation_id += 1
    action = ActionEnvelope.model_validate(
        {
            "observation_id": before.observation_id,
            "action": {"type": "buy", "offer_id": before.state.offers[0].id},
        }
    ).action
    receipt = last_action(before, after, action).transaction
    assert receipt.quoted_cash_charge is None and receipt.net_cash_change is None
    assert receipt.cash_before is None and receipt.cash_after is None


def test_receipt_is_journaled_exported_checkpointed_and_temporally_gated(store, episode):
    observations = [
        Observation.model_validate(e["payload"])
        for e in store.events(episode)
        if e["type"] == "observation"
    ]
    after = next(o for o in observations if o.last_action and o.last_action.action_type == "buy")
    receipt = after.last_action.transaction.model_dump(mode="json")
    assert receipt["quoted_cash_charge"] is not None and receipt["actual_cash_charge"] is None
    checkpoint = json.loads(
        (store.episode_path(episode, True) / f"checkpoint-{after.observation_id}.json").read_text()
    )
    assert checkpoint["observation"]["last_action"]["transaction"] == receipt
    review = ReviewService(store)
    session = review.open(episode)
    token = session["review_token"]
    for _ in range(after.last_action.from_observation_id * 3):
        review.advance(token)
    before = review.view(token)
    assert before["stage"] == "observation" and "transition" not in before
    assert before["observation"]["last_action"]["transaction"] is None
    assert "transition" not in review.advance(token)
    transition = review.advance(token)["transition"]
    assert transition["last_action"]["transaction"] == receipt
    exported = episode_export(store, episode)
    exported_after = next(
        e["payload"]
        for e in exported["events"]
        if e["type"] == "observation" and e["observation_id"] == after.observation_id
    )
    assert exported_after["last_action"]["transaction"] == receipt
