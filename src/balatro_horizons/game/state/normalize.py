"""Pure normalization of a private native engine state."""

from .cards import AREAS, cards, convert_card
from .legal import actions_for_phase, add_inventory_actions, is_pack_phase


def _base_visible(raw, phase, blind, round_state):
    return {
        "phase": phase,
        "progress": {
            "ante": raw.get("ante_num"),
            "blind": blind.get("blind_on_deck"),
            "native_status": phase,
            "round_number": raw.get("round_num"),
        },
        "resources": {
            "money": raw.get("money"),
            "hands": round_state.get("hands_left"),
            "discards": round_state.get("discards_left"),
            "chips": round_state.get("chips"),
            "target": blind.get("target"),
            "joker_capacity": (raw.get("jokers") or {}).get("limit"),
            "consumable_capacity": (raw.get("consumables") or {}).get("limit"),
            "credit_limit": blind.get("credit_limit", 0),
            "shop_reroll_cost": round_state.get("reroll_cost"),
            "boss_reroll_cost": 10 if blind.get("boss_reroll_available") else None,
            "pack_choices_remaining": blind.get("pack_choices"),
        },
        "max_play_cards": (raw.get("hand") or {}).get("highlighted_limit", 5),
        "offers": [],
        "revealed_blinds": [],
        "hand_levels": {},
        "persistent_effects": [],
        "deck_knowledge": {
            "initial_count": None,
            "observed_draws": None,
            "composition": blind.get("deck_composition", {}),
            "remaining_exact": (raw.get("cards") or {}).get("count"),
            "provenance": "native_deck_view",
        },
    }


def _add_cards(visible, raw):
    for public, area in AREAS.items():
        visible[public] = [convert_card(card, area, i) for i, card in enumerate(cards(raw, area))]


def _add_hand_levels(visible, raw):
    for key, value in (raw.get("hands") or {}).items():
        if isinstance(value, dict):
            visible["hand_levels"][key] = ", ".join(
                f"{name}: {value[name]}"
                for name in ("level", "chips", "mult", "played")
                if name in value
            )


def _add_blinds(visible, raw, phase, blind):
    current = (blind.get("blind_on_deck") or "").lower()
    ordered = sorted(
        raw.get("blinds") or {},
        key=lambda key: (key != current, {"small": 0, "big": 1, "boss": 2}.get(key, 3)),
    )
    for key in ordered:
        item = raw["blinds"][key]
        visible["revealed_blinds"].append(
            {
                "native_id": key,
                "label": item.get("name", "Unknown"),
                "kind": key.upper(),
                "target": item.get("score"),
                "skip_allowed": key != "boss" and key == current and phase == "BLIND_SELECT",
                "effects": [str(item["effect"])] if item.get("effect") else [],
                "status": item.get("status", "UNKNOWN"),
                "disabled": blind.get("blind_disabled")
                if key == current and phase == "SELECTING_HAND"
                else None,
                "skip_reward": _skip_reward(item, key),
            }
        )


def _skip_reward(blind, key):
    if key == "boss" or not blind.get("tag_name"):
        return None
    return {
        "label": str(blind["tag_name"]),
        "effects": [str(blind["tag_effect"])] if blind.get("tag_effect") else [],
    }


def convert_offers(raw, phase):
    """Convert phase-specific shop or pack cards to public offers."""
    areas = (
        ["pack"]
        if is_pack_phase(phase)
        else ["shop", "vouchers", "packs"]
        if phase == "SHOP"
        else []
    )
    offers = []
    for area in areas:
        for index, card in enumerate(cards(raw, area)):
            converted = convert_card(card, area, index)
            consumable = card.get("set") in ("TAROT", "PLANET", "SPECTRAL")
            kind = (
                "consumable"
                if consumable
                else "joker"
                if card.get("set") == "JOKER"
                else "pack"
                if area == "packs"
                else "voucher"
                if area == "vouchers"
                else "playing_card"
            )
            offers.append(
                {
                    "native_id": converted["native_id"],
                    "label": converted["label"],
                    "face_down": converted["face_down"],
                    "rank": converted["rank"],
                    "suit": converted["suit"],
                    "kind": kind,
                    "price": 0 if is_pack_phase(phase) else (card.get("cost") or {}).get("buy"),
                    "effects": converted["effects"],
                    "min_targets": (
                        converted["min_targets"] if is_pack_phase(phase) or consumable else 0
                    ),
                    "max_targets": (
                        converted["max_targets"] if is_pack_phase(phase) or consumable else 0
                    ),
                    "acquire_allowed": (
                        bool(card.get("usable"))
                        if is_pack_phase(phase) and consumable
                        else bool(card.get("acquire_allowed", True))
                        if kind in ("joker", "consumable")
                        else True
                    ),
                    "buy_and_use_allowed": consumable and bool(card.get("usable")),
                }
            )
    return offers


def _add_metadata(visible, raw, phase, blind):
    visible["owned_vouchers"] = blind.get("owned_vouchers")
    visible["settlement"] = blind.get("settlement") if phase == "ROUND_EVAL" else None
    if visible["owned_vouchers"] is None:
        visible["owned_vouchers"] = [
            {"label": str(key), "effects": [value] if isinstance(value, str) and value else []}
            for key, value in sorted((raw.get("used_vouchers") or {}).items())
        ]
    visible["pending_tags"] = [
        tag if isinstance(tag, dict) else {"label": str(tag), "effects": []}
        for tag in blind.get("pending_tags", blind.get("tags", []))
    ]


def assemble_visible(raw):
    """Assemble the normalized visible projection without changing raw state."""
    phase, blind = raw["state"], raw["bh"]
    round_state = raw.get("round") or {}
    visible = _base_visible(raw, phase, blind, round_state)
    _add_cards(visible, raw)
    _add_hand_levels(visible, raw)
    _add_metadata(visible, raw, phase, blind)
    _add_blinds(visible, raw, phase, blind)
    visible["offers"] = convert_offers(raw, phase)
    actions = actions_for_phase(phase, blind, round_state)
    reorderable = add_inventory_actions(actions, phase, raw.get("won"), visible)
    if phase not in ("MENU", "GAME_OVER") and not raw.get("won"):
        visible["reorder_areas"] = reorderable
    visible["available_action_types"] = actions
    return visible


def normalize(raw):
    """Normalize private native state while retaining the raw engine payload."""
    return {"visible": assemble_visible(raw), "raw_engine": raw}
