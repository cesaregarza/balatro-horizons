"""Offline legality and successful-action pins for ``bh_action``."""

import pytest
from native_dispatch_harness import NativeHarness, assert_error_unchanged


@pytest.mark.parametrize(
    "code,setup,params",
    [
        ("NOT_READY", "G.STATE_COMPLETE=false", {"action": "skip_pack"}),
        ("UNKNOWN_CARD", "", {"action": "buy", "area": "shop", "index": 0}),
        (
            "INVALID_TARGET_COUNT",
            "G.consumeables.cards={make_card({consumeable=true, "
            "requirements={min_highlighted=1,max_highlighted=1}})}",
            {"action": "use_consumable", "area": "consumables", "index": 0},
        ),
        (
            "SELL_NOT_AVAILABLE",
            "G.jokers.cards={make_card({sellable=false})}",
            {"action": "sell", "area": "jokers", "index": 0},
        ),
        (
            "UNAFFORDABLE",
            "G.GAME.dollars=5; G.GAME.bankrupt_at=2; G.shop_jokers.cards={make_card({cost=4})}",
            {"action": "buy", "area": "shop", "index": 0},
        ),
        (
            "CAPACITY",
            "G.jokers.config.card_limit=1; G.jokers.cards={make_card()}; "
            "G.shop_jokers.cards={make_card({id='offer'})}",
            {"action": "buy", "area": "shop", "index": 0},
        ),
        (
            "CAPACITY",
            "pack_space=false; G.pack_cards.cards={make_card({set='Joker'})}",
            {"action": "choose_pack", "area": "pack", "index": 0},
        ),
        (
            "INVALID_CARD_COUNT",
            "G.hand.cards={make_card({id='hand'})}",
            {"action": "play_hand", "cards": []},
        ),
        (
            "FORCED_CARD_REQUIRED",
            "G.hand.cards={make_card({id='forced',forced_selection=true}),make_card({id='other'})}",
            {"action": "play_hand", "cards": [1]},
        ),
        ("PACK_NOT_OPEN", "G.pack_cards=nil", {"action": "skip_pack"}),
        ("REROLL_NOT_AVAILABLE", "", {"action": "reroll_boss"}),
        ("UNKNOWN_ACTION", "", {"action": "teleport"}),
    ],
)
def test_action_rejections_leave_entire_game_table_unchanged(code, setup, params):
    harness = NativeHarness()
    if setup:
        harness.execute(setup)
    response = assert_error_unchanged(harness, code, "bh_action", params)
    assert response.name == "NOT_ALLOWED_NAME"
    assert harness.eval("action_count()") == 0


@pytest.mark.parametrize("targets", [[99], [0, 0]], ids=["missing", "duplicate"])
def test_invalid_consumable_targets_do_not_mutate_game(targets):
    harness = NativeHarness()
    harness.execute(
        "G.hand.cards={make_card({id='target'})}; "
        "G.consumeables.cards={make_card({consumeable=true, "
        "requirements={min_highlighted=1,max_highlighted=2}})}"
    )
    response = assert_error_unchanged(
        harness,
        "INVALID_TARGETS",
        "bh_action",
        {"action": "use_consumable", "area": "consumables", "index": 0, "targets": targets},
    )
    assert response.name == "NOT_ALLOWED_NAME"


@pytest.mark.parametrize("cards", [[99], [0, 0]], ids=["missing", "duplicate"])
def test_invalid_hand_selection_does_not_mutate_game(cards):
    harness = NativeHarness()
    harness.execute("G.hand.cards={make_card({id='target'})}")
    assert_error_unchanged(
        harness,
        "INVALID_SELECTION",
        "bh_action",
        {"action": "play_hand", "cards": cards},
    )


def test_unusable_consumable_restores_original_highlight_table():
    harness = NativeHarness()
    harness.execute(
        "G.hand.cards={make_card({id='target'})}; "
        "G.hand.highlighted={G.hand.cards[1]}; original_highlighted=G.hand.highlighted; "
        "G.consumeables.cards={make_card({consumeable=true,usable=false, "
        "requirements={min_highlighted=1,max_highlighted=1}})}"
    )
    assert_error_unchanged(
        harness,
        "USE_NOT_AVAILABLE",
        "bh_action",
        {
            "action": "use_consumable",
            "area": "consumables",
            "index": 0,
            "targets": [0],
        },
    )
    assert harness.eval("rawequal(G.hand.highlighted, original_highlighted)") is True


SUCCESS_CASES = [
    (
        "buy",
        "G.shop_jokers.cards={make_card({id='offer'})}",
        {"action": "buy", "area": "shop", "index": 0},
        "buy_from_shop",
        [],
        "offer",
        None,
    ),
    (
        "sell",
        "G.jokers.cards={make_card({id='owned'})}",
        {"action": "sell", "area": "jokers", "index": 0},
        "sell_card",
        [],
        "owned",
        None,
    ),
    (
        "use_consumable",
        "G.hand.cards={make_card({id='target'})}; "
        "G.consumeables.cards={make_card({id='consumable',consumeable=true, "
        "requirements={min_highlighted=1,max_highlighted=1}})}",
        {
            "action": "use_consumable",
            "area": "consumables",
            "index": 0,
            "targets": [0],
        },
        "use_card",
        ["target"],
        "consumable",
        None,
    ),
    (
        "choose_pack",
        "G.hand.cards={make_card({id='target'})}; "
        "G.pack_cards.cards={make_card({id='choice',set='TAROT',consumeable=true, "
        "requirements={min_highlighted=1,max_highlighted=1}})}",
        {"action": "choose_pack", "area": "pack", "index": 0, "targets": [0]},
        "use_card",
        ["target"],
        "choice",
        None,
    ),
    (
        "play_hand",
        "G.hand.cards={make_card({id='left'}),make_card({id='right'})}",
        {"action": "play_hand", "cards": [1, 0]},
        "play_cards_from_highlighted",
        ["right", "left"],
        None,
        None,
    ),
    (
        "discard",
        "G.hand.cards={make_card({id='left'}),make_card({id='right'})}",
        {"action": "discard", "cards": [0]},
        "discard_cards_from_highlighted",
        ["left"],
        None,
        None,
    ),
    (
        "skip_pack",
        "",
        {"action": "skip_pack"},
        "skip_booster",
        [],
        None,
        None,
    ),
    (
        "reroll_boss",
        "G.STATE=G.STATES.BLIND_SELECT; G.GAME.dollars=10; G.GAME.used_vouchers.v_retcon=true",
        {"action": "reroll_boss"},
        "reroll_boss",
        [],
        None,
        None,
    ),
]


@pytest.mark.parametrize(
    "family,setup,params,expected_call,expected_highlights,expected_card,expected_mode",
    SUCCESS_CASES,
    ids=[case[0] for case in SUCCESS_CASES],
)
def test_successful_actions_call_native_family_and_park_pending(
    family,
    setup,
    params,
    expected_call,
    expected_highlights,
    expected_card,
    expected_mode,
):
    harness = NativeHarness()
    if setup:
        harness.execute(setup)
    assert harness.request("bh_action", params, request_id=family) is None
    assert harness.eval("action_count()") == 1
    call = harness.globals.call_log[1]
    assert call.name == expected_call
    assert [call.highlighted[i] for i in range(1, len(call.highlighted) + 1)] == expected_highlights
    assert (call.card.id if call.card is not None else None) == expected_card
    assert call.mode == expected_mode

    harness.advance(39)
    assert harness.eval("response_count()") == 0
    harness.advance(1)
    assert harness.globals.responses[1].marker == "INSPECTED_STATE"
