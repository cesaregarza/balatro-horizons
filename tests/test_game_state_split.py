"""Focused parity checks for the game-state split."""

import hashlib
from pathlib import Path

from balatro_horizons.game.state.normalize import normalize


def _raw(phase):
    return {
        "state": phase,
        "ante_num": 2,
        "round_num": 7,
        "money": 11,
        "won": False,
        "bh": {
            "blind_on_deck": "small",
            "target": "300",
            "credit_limit": 2,
            "boss_reroll_available": True,
            "pack_choices": 2,
            "deck_composition": {"A": 4},
            "owned_vouchers": None,
            "used_vouchers": {"v": "discount"},
            "pending_tags": ["tag", {"label": "T", "effects": ["e"]}],
            "blind_disabled": True,
            "settlement": {"x": 1},
        },
        "round": {"hands_left": 3, "discards_left": 1, "chips": "5", "reroll_cost": 4},
        "hand": {
            "highlighted_limit": 5,
            "cards": [
                {
                    "id": 1,
                    "value": {"rank": "A", "suit": "H"},
                    "state": {},
                    "modifier": {"bonus": 2},
                    "cost": {"sell": 1},
                }
            ],
        },
        "jokers": {
            "limit": 5,
            "cards": [
                {
                    "id": 2,
                    "label": "J",
                    "set": "JOKER",
                    "state": {"debuff": True},
                    "sellable": True,
                }
            ],
        },
        "consumables": {
            "limit": 2,
            "cards": [{"id": 3, "label": "Tarot", "set": "TAROT", "usable": True}],
        },
        "cards": {"count": 40},
        "hands": {"Pair": {"level": 1, "chips": 10, "mult": 2, "played": 4}},
        "blinds": {
            "small": {"name": "Small", "score": 100, "effect": "x", "tag_name": "Tag"},
            "boss": {"name": "Boss", "score": 500},
        },
        "shop": {"cards": [{"id": 4, "set": "JOKER", "label": "Shop J", "cost": {"buy": 4}}]},
        "vouchers": {"cards": [{"id": 5, "set": "VOUCHER", "label": "Voucher"}]},
        "packs": {"cards": [{"id": 6, "set": "STANDARD", "label": "Pack"}]},
        "pack": {
            "cards": [{"id": 7, "set": "TAROT", "label": "Pack Tarot", "usable": True}]
        },
    }


def test_normalize_pins_phase_actions_and_offer_families():
    expected_actions = {
        "MENU": [],
        "BLIND_SELECT": ["select_blind", "skip_blind", "reroll_boss", "sell", "use_consumable"],
        "SELECTING_HAND": ["play_hand", "discard", "sell", "use_consumable"],
        "ROUND_EVAL": ["cash_out", "sell", "use_consumable"],
        "SHOP": ["buy", "reroll_shop", "leave_shop", "sell", "use_consumable"],
        "SMODS_BOOSTER_OPENED": ["choose_pack", "skip_pack", "sell", "use_consumable"],
        "TAROT_PACK": ["choose_pack", "skip_pack", "sell", "use_consumable"],
        "GAME_OVER": [],
    }
    for phase, actions in expected_actions.items():
        visible = normalize(_raw(phase))["visible"]
        assert visible["available_action_types"] == actions
        assert ("reorder_areas" in visible) == (phase not in ("MENU", "GAME_OVER"))

    shop = normalize(_raw("SHOP"))["visible"]["offers"]
    assert [(offer["kind"], offer["price"]) for offer in shop] == [
        ("joker", 4),
        ("voucher", None),
        ("pack", None),
    ]
    pack = normalize(_raw("TAROT_PACK"))["visible"]["offers"]
    assert [(offer["kind"], offer["price"]) for offer in pack] == [("consumable", 0)]


def test_game_fake_is_a_byte_identical_copy():
    root = Path(__file__).parents[1] / "src" / "balatro_horizons"
    fake = (root / "game" / "fake.py").read_bytes()
    assert hashlib.sha256(fake).hexdigest() == (
        "a8d9186475bed552a0130112d21900a36f35cc5244d183c0a3d74fa6cbcdb020"
    )
