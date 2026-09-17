"""Current game-money quotes copied from the canonical public observation."""


def current_costs(observation):
    state = observation["state"]
    resources = state["resources"]
    available = observation["available_action_types"]
    result = {
        "version": "public_costs_v1",
        "observation_id": observation["observation_id"],
        "currency": "game_dollars",
        "cash_balance": resources["money"],
        "credit_limit": resources["credit_limit"],
        "note": (
            "Current displayed prices, including active effects, for the next action only. "
            "A reroll is free only when its cash_cost is 0; recheck after each action. "
            "null means unknown, not free. Action constraints still apply."
        ),
    }
    rerolls = {}
    for action, phase, field in (
        ("reroll_shop", "SHOP", "shop_reroll_cost"),
        ("reroll_boss", "BLIND_SELECT", "boss_reroll_cost"),
    ):
        if observation["phase"] == phase:
            rerolls[action] = {"cash_cost": resources[field], "offered": action in available}
    if rerolls:
        result["rerolls"] = rerolls
    purchase = (
        "buy" if "buy" in available else "choose_pack" if "choose_pack" in available else None
    )
    if purchase:
        result["offers"] = [
            {
                "offer_id": offer["id"],
                "label": "Face-down card" if offer.get("face_down") else offer["label"],
                "cash_cost": offer["price"],
                "operation": purchase,
                "acquire_allowed": offer["acquire_allowed"],
                **(
                    {"buy_and_use_allowed": offer["buy_and_use_allowed"]}
                    if purchase == "buy"
                    else {}
                ),
            }
            for offer in state["offers"]
        ]
    if "sell" in available:
        result["sales"] = [
            {"owned_id": card["id"], "label": card["label"], "cash_received": card["sell_price"]}
            for area in ("jokers", "consumables")
            for card in state[area]
            if not card["face_down"] and card["sellable"] is True
        ]
    return result
