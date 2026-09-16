"""Private native normalization; allowlisted projection is the second boundary."""

AREAS = {"hand": "hand", "jokers": "jokers", "consumables": "consumables"}
SUITS = {"H": "Hearts", "D": "Diamonds", "C": "Clubs", "S": "Spades"}
REORDER_PHASES = {"SELECTING_HAND", "SHOP", "SMODS_BOOSTER_OPENED", "BLIND_SELECT", "ROUND_EVAL"}
HAND_REORDER_PHASES = {"SELECTING_HAND", "SMODS_BOOSTER_OPENED"}


def cards(state, area):
    return (state.get(area) or {}).get("cards") or []


def convert_card(card, area, index):
    value = card.get("value") or {}
    hidden = bool((card.get("state") or {}).get("hidden"))
    effects = [value["effect"]] if value.get("effect") else []
    effects.extend(
        f"{k}: {v}"
        for k, v in (card.get("modifier") or {}).items()
        if type(v) in (str, int, float, bool)
    )
    if (card.get("state") or {}).get("debuff"):
        effects.append("Debuffed")
    rank, suit = value.get("rank"), SUITS.get(value.get("suit"), value.get("suit"))
    stone = (card.get("modifier") or {}).get("enhancement") == "STONE"
    if stone or card.get("rank_visible") is False:
        rank = None
    if stone or card.get("suit_visible") is False:
        suit = None
    # Instrumentation used to overwrite these with e.g. "Base Card" or "Bonus Card".
    # Playing-card identity is the visible rank/suit; enhancements remain effects.
    label = (
        f"{rank} of {suit}" if rank and suit else card.get("label") or card.get("set", "Unknown")
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


def normalize(raw):
    phase, bh = raw["state"], raw["bh"]
    rnd = raw.get("round") or {}
    visible = {
        "phase": phase,
        "progress": {
            "ante": raw.get("ante_num"),
            "blind": bh.get("blind_on_deck"),
            "native_status": phase,
            "round_number": raw.get("round_num"),
        },
        "resources": {
            "money": raw.get("money"),
            "hands": rnd.get("hands_left"),
            "discards": rnd.get("discards_left"),
            "chips": rnd.get("chips"),
            "target": bh.get("target"),
            "joker_capacity": (raw.get("jokers") or {}).get("limit"),
            "consumable_capacity": (raw.get("consumables") or {}).get("limit"),
            "credit_limit": bh.get("credit_limit", 0),
            "shop_reroll_cost": rnd.get("reroll_cost"),
            "boss_reroll_cost": 10 if bh.get("boss_reroll_available") else None,
            "pack_choices_remaining": bh.get("pack_choices"),
        },
        "max_play_cards": (raw.get("hand") or {}).get("highlighted_limit", 5),
        "offers": [],
        "revealed_blinds": [],
        "hand_levels": {},
        "persistent_effects": [],
        "deck_knowledge": {
            "initial_count": None,
            "observed_draws": None,
            "composition": bh.get("deck_composition", {}),
            "remaining_exact": (raw.get("cards") or {}).get("count"),
            "provenance": "native_deck_view",
        },
    }
    for public, area in AREAS.items():
        visible[public] = [convert_card(c, area, i) for i, c in enumerate(cards(raw, area))]
    for key, value in (raw.get("hands") or {}).items():
        if isinstance(value, dict):
            visible["hand_levels"][key] = ", ".join(
                f"{k}: {value[k]}" for k in ("level", "chips", "mult", "played") if k in value
            )
    visible["owned_vouchers"] = bh.get("owned_vouchers")
    if visible["owned_vouchers"] is None:
        # Older captured engine states may contain only keys and optional descriptions.
        visible["owned_vouchers"] = [
            {"label": str(key), "effects": [value] if isinstance(value, str) and value else []}
            for key, value in sorted((raw.get("used_vouchers") or {}).items())
        ]
    visible["pending_tags"] = [
        tag if isinstance(tag, dict) else {"label": str(tag), "effects": []}
        for tag in bh.get("pending_tags", bh.get("tags", []))
    ]
    current = (bh.get("blind_on_deck") or "").lower()
    for key in sorted(
        raw.get("blinds") or {},
        key=lambda k: (k != current, {"small": 0, "big": 1, "boss": 2}.get(k, 3)),
    ):
        blind = raw["blinds"][key]
        visible["revealed_blinds"].append(
            {
                "native_id": key,
                "label": blind.get("name", "Unknown"),
                "kind": key.upper(),
                "target": blind.get("score"),
                "skip_allowed": key != "boss" and key == current and phase == "BLIND_SELECT",
                "effects": [str(blind["effect"])] if blind.get("effect") else [],
                "status": blind.get("status", "UNKNOWN"),
                "disabled": bh.get("blind_disabled")
                if key == current and phase == "SELECTING_HAND"
                else None,
                "skip_reward": (
                    {
                        "label": str(blind["tag_name"]),
                        "effects": [str(blind["tag_effect"])] if blind.get("tag_effect") else [],
                    }
                    if key != "boss" and blind.get("tag_name")
                    else None
                ),
            }
        )
    pack_phase = phase in (
        "SMODS_BOOSTER_OPENED",
        "TAROT_PACK",
        "SPECTRAL_PACK",
        "PLANET_PACK",
        "STANDARD_PACK",
        "BUFFOON_PACK",
    )
    offer_areas = (
        ["pack"] if pack_phase else ["shop", "vouchers", "packs"] if phase == "SHOP" else []
    )
    for area in offer_areas:
        for i, card in enumerate(cards(raw, area)):
            c = convert_card(card, area, i)
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
            visible["offers"].append(
                {
                    "native_id": c["native_id"],
                    "label": c["label"],
                    "face_down": c["face_down"],
                    "rank": c["rank"],
                    "suit": c["suit"],
                    "kind": kind,
                    "price": 0 if pack_phase else (card.get("cost") or {}).get("buy"),
                    "effects": c["effects"],
                    "min_targets": c["min_targets"] if pack_phase or consumable else 0,
                    "max_targets": c["max_targets"] if pack_phase or consumable else 0,
                    "acquire_allowed": bool(card.get("usable"))
                    if pack_phase and consumable
                    else bool(card.get("acquire_allowed", True))
                    if kind in ("joker", "consumable")
                    else True,
                    "buy_and_use_allowed": consumable and bool(card.get("usable")),
                }
            )
    actions = []
    if phase == "BLIND_SELECT":
        actions = ["select_blind"]
        if current != "boss":
            actions.append("skip_blind")
        if bh.get("boss_reroll_available"):
            actions.append("reroll_boss")
    elif phase == "SELECTING_HAND":
        actions = ["play_hand"]
        if (rnd.get("discards_left") or 0) > 0:
            actions.append("discard")
    elif phase == "ROUND_EVAL":
        actions = ["cash_out"]
    elif phase == "SHOP":
        actions = ["buy", "reroll_shop", "leave_shop"]
    elif pack_phase:
        actions = ["choose_pack", "skip_pack"]
    if phase not in ("MENU", "GAME_OVER") and not raw.get("won"):
        if any(c["sellable"] or c["face_down"] for c in visible["jokers"] + visible["consumables"]):
            actions.append("sell")
        if any(c["usable"] for c in visible["consumables"]):
            actions.append("use_consumable")
        visible["reorder_areas"] = [
            area
            for area in AREAS
            if len(visible[area]) > 1
            and phase in (HAND_REORDER_PHASES if area == "hand" else REORDER_PHASES)
        ]
        if visible["reorder_areas"]:
            actions.append("reorder")
    visible["available_action_types"] = actions
    return {"visible": visible, "raw_engine": raw}
