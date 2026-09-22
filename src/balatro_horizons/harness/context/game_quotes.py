"""Current public quotes and mode checks, without strategy or inferred charges."""

from balatro_horizons.actions.validation import InvalidAction, amount, can_afford, validate_action
from balatro_horizons.contracts import ActionEnvelope, Observation


def affordability(price, resources):
    try:
        return can_afford(price, resources.money, resources.credit_limit)
    except InvalidAction:
        return None


def mode_check(observation, action, *, needs_targets=False):
    envelope = ActionEnvelope.model_validate(
        {"observation_id": observation.observation_id, "action": action}
    )
    try:
        validate_action(envelope, observation)
    except InvalidAction as error:
        status = "unavailable"
        if error.code == "VISIBLE_RESOURCE_INCONSISTENCY":
            status = "unknown"
        elif needs_targets and error.code == "INVALID_TARGET_COUNT":
            status = "requires_targets"
        return {"status": status, "reason": error.code}
    return {"status": "legal"}


def obligations(card):
    # The adapter exposes the sticker, not an effective rental charge or timing.
    # Do not infer a rate from card names or invent a lifetime cost.
    if not card.face_down and any(e.strip().lower() == "rental: true" for e in card.effects):
        return [{"kind": "rental", "amount": None, "timing": None, "source": "public_rental_flag"}]
    return []


def inventory(cards, capacity):
    return {
        "used": len(cards),
        "capacity": capacity,
        "open_slots": max(0, capacity - len(cards)) if capacity is not None else None,
    }


def spending_headroom(resources):
    try:
        return str(max(0, amount(resources.money) + amount(resources.credit_limit)))
    except InvalidAction:
        return None


def quote_note():
    return (
        "Current upfront quotes for this observation only; recheck after each action. "
        "Only a zero quote means free upfront. null means unknown. "
        "Rental obligations are separate. Mode checks use empty target_ids."
    )


def reroll_quotes(observation):
    resources, available = observation.state.resources, observation.available_action_types
    rerolls = {}
    for action, phase, field in (
        ("reroll_shop", "SHOP", "shop_reroll_cost"),
        ("reroll_boss", "BLIND_SELECT", "boss_reroll_cost"),
    ):
        if observation.phase == phase:
            price = getattr(resources, field)
            rerolls[action] = {
                "cash_cost": price,
                "offered": action in available,
                "affordable": affordability(price, resources),
                "mode_check": mode_check(observation, {"type": action}),
            }
            if action == "reroll_shop":
                rerolls[action]["free_rerolls_remaining"] = None
    return rerolls


def offer_quote(observation, offer, purchase):
    action = {"type": purchase, "offer_id": offer.id}
    modes = {
        "acquire" if purchase == "buy" else "choose_pack": mode_check(
            observation, action,
            needs_targets=purchase == "choose_pack" and offer.min_targets > 0,
        )
    }
    if purchase == "buy":
        modes["buy_and_use"] = mode_check(
            observation, {**action, "mode": "buy_and_use"},
            needs_targets=offer.min_targets > 0,
        )
    return {
        "offer_id": offer.id,
        "label": "Face-down card" if offer.face_down else offer.label,
        "kind": "unknown" if offer.face_down else offer.kind,
        "effects": [] if offer.face_down else list(offer.effects),
        "cash_cost": offer.price,
        "affordable": affordability(offer.price, observation.state.resources),
        "operation": purchase,
        "purchase_modes": modes,
        "target_count": {"min": offer.min_targets, "max": offer.max_targets},
        "recorded_obligations": obligations(offer),
    }


def sale_quotes(observation):
    return [
        {
            "owned_id": card.id,
            "label": card.label,
            "kind": kind,
            "quoted_sale_proceeds": card.sell_price,
            "sell_allowed": card.sellable,
            "mode_check": mode_check(observation, {"type": "sell", "owned_id": card.id}),
            "recorded_obligations": obligations(card),
        }
        for kind, cards in (("joker", observation.state.jokers),
                            ("consumable", observation.state.consumables))
        for card in cards if not card.face_down
    ]


def current_costs(public):
    observation = Observation.model_validate(public)
    state, available = observation.state, observation.available_action_types
    resources = state.resources
    result = {
        "version": "public_costs_v2", "observation_id": observation.observation_id,
        "currency": "game_dollars", "cash_balance": resources.money,
        "credit_limit": resources.credit_limit,
        "positive_price_spending_headroom": spending_headroom(resources),
        "inventory": {
            "jokers": inventory(state.jokers, resources.joker_capacity),
            "consumables": inventory(state.consumables, resources.consumable_capacity),
        },
        "note": quote_note(),
    }
    if rerolls := reroll_quotes(observation):
        result["rerolls"] = rerolls
    purchase = "buy" if "buy" in available else "choose_pack" if "choose_pack" in available else None
    if purchase:
        result["offers"] = [offer_quote(observation, offer, purchase) for offer in state.offers]
    if "sell" in available or purchase:
        result["owned_items"] = sale_quotes(observation)
    return result
