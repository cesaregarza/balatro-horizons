"""Translate validated public actions into one native RPC request."""

from collections.abc import Callable
from typing import Any

from balatro_horizons.game.contract import NativeRejected
from balatro_horizons.game.state.cards import cards


def locate(raw: dict[str, Any], handle: str, public_area: str, issuer) -> tuple[str, int]:
    """Only the issuer may resolve a public handle to a native card index."""
    key = issuer.resolve_current(handle, public_area)
    area, native_id = key.split(":", 1)
    for index, card in enumerate(cards(raw, area)):
        if str(card.get("id", index)) == native_id:
            return area, index
    raise NativeRejected("STALE_NATIVE_OBJECT")


def _simple(method: str) -> Callable:
    return lambda action, raw, issuer: (method, {})


def _hand(action, raw, issuer):
    return "bh_action", {
        "action": action.type,
        "cards": [locate(raw, handle, "hand", issuer)[1] for handle in action.card_ids],
    }


def _reorder(action, raw, issuer):
    return "rearrange", {
        action.area: [locate(raw, handle, action.area, issuer)[1]
                      for handle in action.ordered_ids]
    }


def _offer(action, raw, issuer):
    if action.type in ("buy", "choose_pack"):
        handle, area = action.offer_id, "offers"
    elif action.type == "use_consumable":
        handle, area = action.consumable_id, "consumables"
    else:
        handle = action.owned_id
        area = issuer.current_area(handle)
    native_area, index = locate(raw, handle, area, issuer)
    params = {"action": action.type, "area": native_area, "index": index}
    if action.type != "sell":
        params["targets"] = [locate(raw, target, "hand", issuer)[1]
                             for target in action.target_ids]
    if action.type == "buy":
        params["mode"] = action.mode
    return "bh_action", params


def _named(action, raw, issuer):
    return "bh_action", {"action": action.type}


# The method table is the only action-family dispatch. New action types must
# deliberately choose one handler; they cannot fall through to a native RPC.
ACTION_HANDLERS = {
    "select_blind": _simple("select"),
    "skip_blind": _simple("skip"),
    "cash_out": _simple("cash_out"),
    "leave_shop": _simple("next_round"),
    "reroll_shop": _simple("reroll"),
    "play_hand": _hand,
    "discard": _hand,
    "reorder": _reorder,
    "buy": _offer,
    "choose_pack": _offer,
    "use_consumable": _offer,
    "sell": _offer,
    "skip_pack": _named,
    "reroll_boss": _named,
}


def native_request(action, raw, issuer):
    """Return exactly one allowlisted method and parameter object."""
    handler = ACTION_HANDLERS.get(action.type)
    if handler is None:
        raise NativeRejected("UNKNOWN_ACTION")
    return handler(action, raw, issuer)
