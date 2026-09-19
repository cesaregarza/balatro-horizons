"""Transparent baselines: public facts only, no engine scoring or lookahead."""

import random
from itertools import combinations

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.contracts import ActionEnvelope, Observation


def candidates(obs):
    hand = [c.id for c in obs.state.hand]
    offered = set(obs.available_action_types)
    raw = []
    if obs.state.revealed_blinds:
        for kind in ("select_blind", "skip_blind"):
            raw.append({"type": kind, "blind_id": obs.state.revealed_blinds[0].id})
    for kind in ("play_hand", "discard"):
        for size in range(1, min(len(hand), 5) + 1):
            raw.extend(
                {"type": kind, "card_ids": list(cards)} for cards in combinations(hand, size)
            )
    for area in ("hand", "jokers", "consumables"):
        ids = [c.id for c in getattr(obs.state, area)]
        if len(ids) > 1:
            raw.append({"type": "reorder", "area": area, "ordered_ids": ids[::-1]})
    for offer in obs.state.offers:
        raw.append({"type": "buy", "offer_id": offer.id})
        for size in range(offer.min_targets, min(offer.max_targets, len(hand)) + 1):
            for ids in combinations(hand, size):
                raw.extend(
                    [
                        {
                            "type": "buy",
                            "offer_id": offer.id,
                            "mode": "buy_and_use",
                            "target_ids": list(ids),
                        },
                        {"type": "choose_pack", "offer_id": offer.id, "target_ids": list(ids)},
                    ]
                )
    for card in obs.state.jokers + obs.state.consumables:
        raw.append({"type": "sell", "owned_id": card.id})
    for card in obs.state.consumables:
        for size in range(card.min_targets, min(card.max_targets, len(hand)) + 1):
            for ids in combinations(hand, size):
                raw.append(
                    {"type": "use_consumable", "consumable_id": card.id, "target_ids": list(ids)}
                )
    raw.extend(
        {"type": kind}
        for kind in ("reroll_shop", "reroll_boss", "skip_pack", "leave_shop", "cash_out")
    )
    for action in raw:
        if action["type"] in offered:
            envelope = ActionEnvelope.model_validate(
                {"observation_id": obs.observation_id, "action": action}
            )
            try:
                validate_action(envelope, obs)
            except InvalidAction:
                continue
            yield envelope


def presented_candidates(observation):
    """Generate legal-looking choices from the single compact harness view."""
    state = observation["state"]
    hand = [card["id"] for card in state["hand"]]
    offered = set(observation["available_action_types"])
    raw = []
    if state["revealed_blinds"]:
        blind_id = state["revealed_blinds"][0]["id"]
        raw.extend(
            {"type": kind, "blind_id": blind_id}
            for kind in ("select_blind", "skip_blind")
        )
    for kind in ("play_hand", "discard"):
        for count in range(1, min(len(hand), 5) + 1):
            raw.extend(
                {"type": kind, "card_ids": list(cards)}
                for cards in combinations(hand, count)
            )
    for area in ("hand", "jokers", "consumables"):
        ids = [card["id"] for card in state[area]]
        if len(ids) > 1:
            raw.append({"type": "reorder", "area": area, "ordered_ids": ids[::-1]})
    for offer in state["offers"]:
        raw.append({"type": "buy", "offer_id": offer["id"]})
        minimum = offer.get("min_targets", 0)
        maximum = min(offer.get("max_targets", 0), len(hand))
        for count in range(minimum, maximum + 1):
            for ids in combinations(hand, count):
                raw.extend(
                    [
                        {
                            "type": "buy",
                            "offer_id": offer["id"],
                            "mode": "buy_and_use",
                            "target_ids": list(ids),
                        },
                        {
                            "type": "choose_pack",
                            "offer_id": offer["id"],
                            "target_ids": list(ids),
                        },
                    ]
                )
    for card in state["jokers"] + state["consumables"]:
        raw.append({"type": "sell", "owned_id": card["id"]})
    for card in state["consumables"]:
        minimum = card.get("min_targets", 0)
        maximum = min(card.get("max_targets", 0), len(hand))
        for count in range(minimum, maximum + 1):
            for ids in combinations(hand, count):
                raw.append(
                    {
                        "type": "use_consumable",
                        "consumable_id": card["id"],
                        "target_ids": list(ids),
                    }
                )
    raw.extend(
        {"type": kind}
        for kind in ("reroll_shop", "reroll_boss", "skip_pack", "leave_shop", "cash_out")
    )
    return [action for action in raw if action["type"] in offered]


class Baseline:
    paid = False

    def __init__(self, name="random_legal", seed=0):
        if name not in ("random_legal", "heuristic"):
            raise ValueError("UNKNOWN_BASELINE")
        self.name = name
        self.rng = random.Random(seed)

    def decide(self, ctx, exchanges):
        presented = ctx["observation"]
        if "schema_version" in presented:
            obs = Observation.model_validate(presented)
            choices = [choice.model_dump(mode="json") for choice in candidates(obs)]
            cards = [card.model_dump(mode="json") for card in obs.state.hand]
        else:
            choices = [
                {"observation_id": presented["observation_id"], "action": action}
                for action in presented_candidates(presented)
            ]
            cards = presented["state"]["hand"]
        if not choices:
            return {"kind": "abort", "reason": "NO_PUBLIC_LEGAL_ACTION_FOUND"}
        if self.name == "random_legal":
            chosen = self.rng.choice(choices)
        else:
            # Prefer play; maximize repeated visible ranks, then visible ranks' sum.
            ranks = {card["id"]: card.get("rank") for card in cards}
            values = {str(i): i for i in range(2, 11)} | {
                "T": 10,
                "J": 11,
                "Q": 12,
                "K": 13,
                "A": 14,
            }
            priority = {
                "select_blind": 10,
                "play_hand": 9,
                "cash_out": 8,
                "buy": 7,
                "choose_pack": 6,
                "use_consumable": 5,
                "leave_shop": 4,
                "skip_pack": 3,
            }

            def key(env):
                action = env["action"]
                ids = action.get("card_ids", [])
                repeats = sum(
                    ranks.get(i) == ranks.get(j) and ranks.get(i) is not None
                    for i, j in combinations(ids, 2)
                )
                return (
                    priority.get(action["type"], 0),
                    repeats,
                    sum(values.get(ranks.get(i), 0) for i in ids),
                )

            chosen = max(choices, key=key)
        return {"kind": "action", "envelope": chosen}


class ScriptedPolicy:
    paid = False

    def __init__(self, operations):
        self.operations = iter(operations)

    def decide(self, ctx, exchanges):
        return next(self.operations)
