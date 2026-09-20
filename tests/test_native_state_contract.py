"""Table-driven pins for native-state action and offer normalization."""

from copy import deepcopy

import pytest
from test_public_information import native_state

from balatro_horizons.game.state import normalize

PACK_PHASES = [
    "SMODS_BOOSTER_OPENED",
    "TAROT_PACK",
    "SPECTRAL_PACK",
    "PLANET_PACK",
    "STANDARD_PACK",
    "BUFFOON_PACK",
]


def card(card_id, *, set_name="JOKER", **values):
    return {
        "id": card_id,
        "label": values.pop("label", card_id),
        "set": set_name,
        "cost": values.pop("cost", {"buy": 7}),
        **values,
    }


ACTION_CASES = [
    ("MENU", {}, []),
    ("GAME_OVER", {}, []),
    ("HAND_PLAYED", {}, []),
    ("BLIND_SELECT", {}, ["select_blind", "skip_blind"]),
    (
        "BLIND_SELECT",
        {"blind_on_deck": "Boss", "boss_reroll_available": True},
        ["select_blind", "reroll_boss"],
    ),
    ("SELECTING_HAND", {"discards_left": 3}, ["play_hand", "discard"]),
    ("SELECTING_HAND", {"discards_left": 0}, ["play_hand"]),
    ("ROUND_EVAL", {}, ["cash_out"]),
    ("SHOP", {}, ["buy", "reroll_shop", "leave_shop"]),
    (
        "SHOP",
        {"sellable": True, "usable": True, "reorder": True},
        ["buy", "reroll_shop", "leave_shop", "sell", "use_consumable", "reorder"],
    ),
    (
        "HAND_PLAYED",
        {"sellable": True, "usable": True, "won": True},
        [],
    ),
    *[(phase, {}, ["choose_pack", "skip_pack"]) for phase in PACK_PHASES],
]


@pytest.mark.parametrize(
    "phase,flags,expected",
    ACTION_CASES,
    ids=[f"{phase}-{index}" for index, (phase, _, __) in enumerate(ACTION_CASES)],
)
def test_available_actions_follow_phase_and_public_flags(phase, flags, expected):
    raw = native_state(phase)
    raw["shop"] = {"cards": []}
    raw["pack"] = {"cards": []}
    raw["bh"]["blind_on_deck"] = flags.get("blind_on_deck", "Big")
    raw["bh"]["boss_reroll_available"] = flags.get("boss_reroll_available", False)
    raw["round"]["discards_left"] = flags.get("discards_left", 3)
    raw["won"] = flags.get("won", False)
    if flags.get("sellable"):
        raw["jokers"]["cards"] = [card("sellable", sellable=True)]
    if flags.get("usable"):
        raw["consumables"]["cards"] = [card("usable", set_name="TAROT", usable=True)]
    if flags.get("reorder"):
        raw["jokers"]["cards"].append(card("second-joker"))

    before = deepcopy(raw)
    assert normalize(raw)["visible"]["available_action_types"] == expected
    assert raw == before


OFFER_CASES = [
    (
        "SHOP",
        "shop",
        card("playing", set_name="ENHANCED"),
        {"kind": "playing_card", "price": 7, "min_targets": 0, "max_targets": 0},
    ),
    (
        "SHOP",
        "shop",
        card("joker", set_name="JOKER", acquire_allowed=False),
        {"kind": "joker", "price": 7, "acquire_allowed": False},
    ),
    (
        "SHOP",
        "shop",
        card("tarot", set_name="TAROT", usable=True, min_targets=1, max_targets=2),
        {
            "kind": "consumable",
            "price": 7,
            "min_targets": 1,
            "max_targets": 2,
            "buy_and_use_allowed": True,
        },
    ),
    (
        "SHOP",
        "vouchers",
        card("voucher", set_name="VOUCHER", cost={"buy": 10}),
        {"kind": "voucher", "price": 10},
    ),
    (
        "SHOP",
        "packs",
        card("booster", set_name="BOOSTER", cost={"buy": 4}),
        {"kind": "pack", "price": 4},
    ),
    (
        "STANDARD_PACK",
        "pack",
        card("pack-joker", set_name="JOKER", cost={"buy": 99}),
        {"kind": "joker", "price": 0},
    ),
    (
        "TAROT_PACK",
        "pack",
        card(
            "pack-consumable",
            set_name="TAROT",
            usable=True,
            acquire_allowed=False,
            min_targets=1,
            max_targets=1,
        ),
        {
            "kind": "consumable",
            "price": 0,
            "min_targets": 1,
            "max_targets": 1,
            "acquire_allowed": True,
            "buy_and_use_allowed": True,
        },
    ),
    (
        "STANDARD_PACK",
        "pack",
        card("pack-card", set_name="ENHANCED", cost={"buy": 99}),
        {"kind": "playing_card", "price": 0},
    ),
]


@pytest.mark.parametrize(
    "phase,area,offer,expected",
    OFFER_CASES,
    ids=[case[2]["id"] for case in OFFER_CASES],
)
def test_offer_kind_and_price_follow_phase_area_and_shape(phase, area, offer, expected):
    raw = native_state(phase)
    for name in ("shop", "vouchers", "packs", "pack"):
        raw[name] = {"cards": []}
    raw[area]["cards"] = [offer]
    before = deepcopy(raw)

    (normalized,) = normalize(raw)["visible"]["offers"]
    for key, value in expected.items():
        assert normalized[key] == value
    assert raw == before
