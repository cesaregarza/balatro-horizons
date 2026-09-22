"""Card-area constants and conversion for the native state boundary."""

AREAS = {"hand": "hand", "jokers": "jokers", "consumables": "consumables"}
SUITS = {"H": "Hearts", "D": "Diamonds", "C": "Clubs", "S": "Spades"}
REORDER_PHASES = {"SELECTING_HAND", "SHOP", "SMODS_BOOSTER_OPENED", "BLIND_SELECT", "ROUND_EVAL"}
HAND_REORDER_PHASES = {"SELECTING_HAND", "SMODS_BOOSTER_OPENED"}


def cards(state, area):
    """Return the cards in an engine area, tolerating absent areas."""
    return (state.get(area) or {}).get("cards") or []


def convert_card(card, area, index):
    """Convert one private engine card to the stable internal card shape."""
    value = card.get("value") or {}
    hidden = bool((card.get("state") or {}).get("hidden"))
    effects = [value["effect"]] if value.get("effect") else []
    effects.extend(
        f"{key}: {modifier}"
        for key, modifier in (card.get("modifier") or {}).items()
        if type(modifier) in (str, int, float, bool)
    )
    if (card.get("state") or {}).get("debuff"):
        effects.append("Debuffed")
    rank, suit = value.get("rank"), SUITS.get(value.get("suit"), value.get("suit"))
    stone = (card.get("modifier") or {}).get("enhancement") == "STONE"
    if stone or card.get("rank_visible") is False:
        rank = None
    if stone or card.get("suit_visible") is False:
        suit = None
    # Playing-card identity is the visible rank/suit; enhancements remain effects.
    label = (
        f"{rank} of {suit}"
        if rank and suit
        else card.get("label") or card.get("set", "Unknown")
    )
    return {
        "native_id": f"{area}:{card.get('id', index)}",
        "label": label,
        "face_down": hidden,
        "trackable": not hidden,
        "rank": rank,
        "suit": suit,
        "effects": effects,
        "counters": card.get("counters") or {},
        "sell_price": (card.get("cost") or {}).get("sell"),
        "sellable": bool(card.get("sellable")),
        "usable": bool(card.get("usable")) and not hidden,
        "min_targets": card.get("min_targets", 0),
        "max_targets": card.get("max_targets", 0),
        "forced_selection": card.get("forced_selection", False),
    }
