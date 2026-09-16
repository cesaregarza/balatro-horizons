"""Reject invalid public operations before touching native state."""

from decimal import Decimal, InvalidOperation

from balatro_horizons.config import MAX_DECISION_NOTE_CHARACTERS, MAX_MEMORY_CHARACTERS
from balatro_horizons.contracts import (
    ActionEnvelope,
    Buy,
    ChoosePack,
    Discard,
    Observation,
    PlayHand,
    Reorder,
    RerollBoss,
    RerollShop,
    SelectBlind,
    Sell,
    SkipBlind,
    UseConsumable,
)


class InvalidAction(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def unique_subset(selected, available):
    return len(selected) == len(set(selected)) and set(selected) <= set(available)


def amount(value):
    try:
        number = Decimal(value) if value is not None else Decimal("NaN")
        if not number.is_finite():
            raise InvalidOperation
        return number
    except (InvalidOperation, TypeError):
        raise InvalidAction("VISIBLE_RESOURCE_INCONSISTENCY") from None


def targets(ids, hand, obj):
    if not unique_subset(ids, hand):
        raise InvalidAction("INVALID_TARGETS")
    if not obj.min_targets <= len(ids) <= obj.max_targets:
        raise InvalidAction("INVALID_TARGET_COUNT")


def validate_action(
    envelope: ActionEnvelope, observation: Observation, *, memory_limit=MAX_MEMORY_CHARACTERS
):
    if envelope.observation_id != observation.observation_id:
        raise InvalidAction("STALE_OBSERVATION")
    action = envelope.action
    if action.type not in observation.available_action_types:
        raise InvalidAction("ACTION_NOT_AVAILABLE")
    if envelope.memory_update is not None and len(envelope.memory_update) > memory_limit:
        raise InvalidAction("MEMORY_TOO_LARGE")
    if (
        envelope.decision_note is not None
        and len(envelope.decision_note) > MAX_DECISION_NOTE_CHARACTERS
    ):
        raise InvalidAction("DECISION_NOTE_TOO_LARGE")
    state = observation.state
    hand = [c.id for c in state.hand]
    owned = {c.id: c for c in state.jokers + state.consumables}
    offers = {c.id: c for c in state.offers}
    if isinstance(action, (SelectBlind, SkipBlind)):
        if not state.revealed_blinds or action.blind_id != state.revealed_blinds[0].id:
            raise InvalidAction("UNKNOWN_BLIND")
        if isinstance(action, SkipBlind) and not state.revealed_blinds[0].skip_allowed:
            raise InvalidAction("ACTION_NOT_AVAILABLE")
    elif isinstance(action, (PlayHand, Discard)):
        limits = observation.action_constraints[action.type]
        if not unique_subset(action.card_ids, hand):
            raise InvalidAction("INVALID_CARD_SELECTION")
        if (
            not int(limits.get("min_cards", 1))
            <= len(action.card_ids)
            <= int(limits.get("max_cards", 5))
        ):
            raise InvalidAction("INVALID_CARD_COUNT")
        if not set(limits.get("required_ids", [])) <= set(action.card_ids):
            raise InvalidAction("FORCED_CARD_REQUIRED")
    elif isinstance(action, Reorder):
        if action.area not in observation.action_constraints.get("reorder", {}).get("areas", []):
            raise InvalidAction("REORDER_AREA_NOT_AVAILABLE")
        area = [c.id for c in getattr(state, action.area)]
        if len(action.ordered_ids) != len(area) or not unique_subset(action.ordered_ids, area):
            raise InvalidAction("INVALID_PERMUTATION")
    elif isinstance(action, Buy):
        offer = offers.get(action.offer_id)
        if offer is None:
            raise InvalidAction("UNKNOWN_OFFER")
        if amount(offer.price) > 0 and amount(offer.price) > amount(state.resources.money) + amount(
            state.resources.credit_limit
        ):
            raise InvalidAction("UNAFFORDABLE")
        if action.mode == "acquire":
            if not offer.acquire_allowed or action.target_ids:
                raise InvalidAction("ACQUISITION_NOT_AVAILABLE")
        else:
            if not offer.buy_and_use_allowed:
                raise InvalidAction("INVALID_ACQUISITION_MODE")
            targets(action.target_ids, hand, offer)
    elif isinstance(action, Sell):
        if action.owned_id not in owned or owned[action.owned_id].sellable is False:
            raise InvalidAction("SELL_NOT_AVAILABLE")
    elif isinstance(action, UseConsumable):
        obj = next((c for c in state.consumables if c.id == action.consumable_id), None)
        if obj is None or not obj.usable:
            raise InvalidAction("USE_NOT_AVAILABLE")
        targets(action.target_ids, hand, obj)
    elif isinstance(action, ChoosePack):
        if action.offer_id not in offers or not offers[action.offer_id].acquire_allowed:
            raise InvalidAction("PACK_CHOICE_NOT_AVAILABLE")
        targets(action.target_ids, hand, offers[action.offer_id])
    elif isinstance(action, (RerollShop, RerollBoss)):
        cost = (
            state.resources.shop_reroll_cost
            if isinstance(action, RerollShop)
            else state.resources.boss_reroll_cost
        )
        if amount(cost) > 0 and amount(cost) > amount(state.resources.money) + amount(
            state.resources.credit_limit
        ):
            raise InvalidAction("UNAFFORDABLE")
