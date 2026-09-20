"""Pin every public-action family to its native method and parameters."""

from types import SimpleNamespace

import pytest

from balatro_horizons.game.actions import ACTION_HANDLERS, native_request

RAW = {
    "hand": {"cards": [{"id": "h0"}, {"id": "h1"}]},
    "jokers": {"cards": [{"id": "j0"}, {"id": "j1"}]},
    "consumables": {"cards": [{"id": "c0"}, {"id": "c1"}]},
    "shop": {"cards": [{"id": "s0"}]},
    "vouchers": {"cards": [{"id": "v0"}]},
    "packs": {"cards": [{"id": "p0"}]},
    "pack": {"cards": [{"id": "pc0"}]},
}
HANDLES = {
    ("hand-0", "hand"): "hand:h0", ("hand-1", "hand"): "hand:h1",
    ("joker-0", "jokers"): "jokers:j0", ("joker-1", "jokers"): "jokers:j1",
    ("consumable-0", "consumables"): "consumables:c0",
    ("consumable-1", "consumables"): "consumables:c1",
    ("shop-0", "offers"): "shop:s0", ("voucher-0", "offers"): "vouchers:v0",
    ("pack-offer-0", "offers"): "packs:p0", ("pack-card-0", "offers"): "pack:pc0",
}


class Issuer:
    def resolve_current(self, handle, area):
        return HANDLES[(handle, area)]

    def current_area(self, handle):
        return next(area for key, area in HANDLES if key == handle)


CASES = [
    ({"type": "select_blind"}, ("select", {})),
    ({"type": "skip_blind"}, ("skip", {})),
    ({"type": "cash_out"}, ("cash_out", {})),
    ({"type": "leave_shop"}, ("next_round", {})),
    ({"type": "reroll_shop"}, ("reroll", {})),
    ({"type": "play_hand", "card_ids": ["hand-1", "hand-0"]},
     ("bh_action", {"action": "play_hand", "cards": [1, 0]})),
    ({"type": "discard", "card_ids": ["hand-0"]},
     ("bh_action", {"action": "discard", "cards": [0]})),
    ({"type": "reorder", "area": "hand", "ordered_ids": ["hand-1", "hand-0"]},
     ("rearrange", {"hand": [1, 0]})),
    ({"type": "reorder", "area": "jokers", "ordered_ids": ["joker-1", "joker-0"]},
     ("rearrange", {"jokers": [1, 0]})),
    ({"type": "reorder", "area": "consumables",
      "ordered_ids": ["consumable-1", "consumable-0"]},
     ("rearrange", {"consumables": [1, 0]})),
    ({"type": "buy", "offer_id": "shop-0", "mode": "acquire", "target_ids": []},
     ("bh_action", {"action": "buy", "area": "shop", "index": 0,
                    "targets": [], "mode": "acquire"})),
    ({"type": "buy", "offer_id": "shop-0", "mode": "buy_and_use",
      "target_ids": ["hand-1"]},
     ("bh_action", {"action": "buy", "area": "shop", "index": 0,
                    "targets": [1], "mode": "buy_and_use"})),
    ({"type": "buy", "offer_id": "voucher-0", "mode": "acquire", "target_ids": []},
     ("bh_action", {"action": "buy", "area": "vouchers", "index": 0,
                    "targets": [], "mode": "acquire"})),
    ({"type": "buy", "offer_id": "pack-offer-0", "mode": "acquire", "target_ids": []},
     ("bh_action", {"action": "buy", "area": "packs", "index": 0,
                    "targets": [], "mode": "acquire"})),
    ({"type": "choose_pack", "offer_id": "pack-card-0", "target_ids": ["hand-0"]},
     ("bh_action", {"action": "choose_pack", "area": "pack", "index": 0,
                    "targets": [0]})),
    ({"type": "use_consumable", "consumable_id": "consumable-0",
      "target_ids": ["hand-1"]},
     ("bh_action", {"action": "use_consumable", "area": "consumables",
                    "index": 0, "targets": [1]})),
    ({"type": "sell", "owned_id": "joker-0"},
     ("bh_action", {"action": "sell", "area": "jokers", "index": 0})),
    ({"type": "sell", "owned_id": "consumable-1"},
     ("bh_action", {"action": "sell", "area": "consumables", "index": 1})),
    ({"type": "skip_pack"}, ("bh_action", {"action": "skip_pack"})),
    ({"type": "reroll_boss"}, ("bh_action", {"action": "reroll_boss"})),
]


@pytest.mark.parametrize("action,expected", CASES, ids=[str(i) for i in range(len(CASES))])
def test_public_action_table_matches_native_request(action, expected):
    assert native_request(SimpleNamespace(**action), RAW, Issuer()) == expected


def test_public_action_table_covers_exactly_the_declared_families():
    assert len(CASES) == 20
    assert {action["type"] for action, _ in CASES} == set(ACTION_HANDLERS)
