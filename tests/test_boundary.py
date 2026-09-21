import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.contracts import ActionEnvelope, RemainingBudget
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.arithmetic import arithmetic
from balatro_horizons.harness.context.build import context
from balatro_horizons.observations.projection import HandleIssuer, project_public


def project(raw, issuer=None, index=0):
    return project_public(
        raw,
        episode_id="e" * 32,
        observation_id=index,
        issuer=issuer or HandleIssuer(b"a" * 32),
        memory="",
        remaining_budget=RemainingBudget(game_actions=1500, provider_calls=2000),
    )


def test_AT01_allowlist():
    game = FakeGame("PRIVATE_SEED_SENTINEL")
    game.phase = "SELECTING_HAND"
    raw = game.observe_private()
    raw["visible"]["seed"] = "PRIVATE_SEED_SENTINEL"
    for card in raw["visible"]["hand"]:
        card["rng_state"] = "PRIVATE_RNG_SENTINEL"
    value = json.dumps(context(project(raw)))
    assert "PRIVATE_" not in value and "forbidden" not in value and "card-internal" not in value


def test_AT02_noninterference_hidden_identities_and_order():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    a = game.observe_private()
    b = deepcopy(a)
    for raw in (a, b):
        for card in raw["visible"]["hand"]:
            card["face_down"] = True
    b["seed"] = "different"
    b["hidden_draw_order"].reverse()
    b["visible"]["hand"].reverse()
    for i, card in enumerate(b["visible"]["hand"]):
        card.update(
            native_id="entirely-different-" + str(i),
            rank="K",
            suit="Diamonds",
            label="Private King",
            effects=["secret"],
        )
    assert project(a) == project(b)


def test_AT03_handles_do_not_reconnect_after_concealment():
    issuer = HandleIssuer(b"a" * 32)
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    raw = game.observe_private()
    before = project(raw, issuer, 0).state.hand[0].id
    raw["visible"]["hand"][0]["face_down"] = True
    hidden = project(raw, issuer, 1).state.hand[0]
    assert hidden.rank is None and hidden.effects == [] and hidden.id != before
    raw["visible"]["hand"][0]["face_down"] = False
    assert project(raw, issuer, 2).state.hand[0].id not in (before, hidden.id)
    raw["visible"]["hand"] = []
    project(raw, issuer, 3)
    raw = game.observe_private()
    assert project(raw, issuer, 4).state.hand[0].id != before


def test_AT04_negative_money_credit_and_large_values():
    game = FakeGame()
    game.phase = "SHOP"
    raw = game.observe_private()
    raw["visible"]["resources"].update(money=-5, credit_limit=20, chips="1e100", joker_capacity=9)
    obs = project(raw)
    assert obs.state.resources.chips == "1e100" and obs.state.resources.money == "-5"
    action = ActionEnvelope.model_validate(
        {"observation_id": 0, "action": {"type": "buy", "offer_id": obs.state.offers[0].id}}
    )
    validate_action(action, obs)
    obs.state.resources.credit_limit = "0"
    with pytest.raises(InvalidAction, match="UNAFFORDABLE"):
        validate_action(action, obs)


def test_AT07_invalid_selection_and_stale_observation_leave_game_unchanged():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    raw = game.observe_private()
    obs = project(raw)
    handle = obs.state.hand[0].id
    for envelope in [
        dict(observation_id=99, action={"type": "play_hand", "card_ids": [handle]}),
        dict(observation_id=0, action={"type": "play_hand", "card_ids": [handle, handle]}),
        dict(observation_id=0, action={"type": "play_hand", "card_ids": ["unknown"]}),
    ]:
        with pytest.raises(InvalidAction):
            validate_action(ActionEnvelope.model_validate(envelope), obs)
    assert game.observe_private() == raw
    with pytest.raises(ValidationError):
        ActionEnvelope.model_validate(
            {"observation_id": 0, "action": {"type": "debug_set", "seed": "no"}}
        )


def test_AT08_order_and_current_handles():
    game = FakeGame()
    game.phase = "SELECTING_HAND"
    raw = game.observe_private()
    issuer = HandleIssuer(b"a" * 32)
    a = project(raw, issuer)
    raw["visible"]["hand"].reverse()
    b = project(raw, issuer, 1)
    assert [c.id for c in b.state.hand] == [c.id for c in a.state.hand][::-1]
    assert issuer.resolve_current(b.state.hand[0].id, "hand") == "card-internal-c"
    assert a.public_state_hash != b.public_state_hash


def test_arithmetic_rejects_code_and_nonfinite():
    assert arithmetic("(5+3)*2/4") == "4"
    for value in [
        '__import__("os")',
        "1/0",
        "2**100000",
        'float("nan")',
        "[1][0]",
        "True",
        "1e101",
    ]:
        with pytest.raises((ValueError, ArithmeticError)):
            arithmetic(value)
