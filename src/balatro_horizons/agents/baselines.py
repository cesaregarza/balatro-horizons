"""Transparent baselines: public facts only, no engine scoring or lookahead."""

import random
from copy import deepcopy
from itertools import combinations

from balatro_horizons.actions.validation import InvalidAction, validate_action
from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.harness.contract import Context


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


def baseline_observation(presented):
    """Restore only omitted public fields so the canonical action validator can run."""
    if "schema_version" in presented:
        return Observation.model_validate(presented)
    state = deepcopy(presented["state"])
    state["public_deck_knowledge"] = {}
    for area in ("hand", "jokers", "consumables"):
        for card in state[area]:
            card.pop("counter_defaults_apply", None)
    return Observation.model_validate(
        {
            "schema_version": presented["public_contract_version"],
            "episode_id": "baseline-public-view",
            "observation_id": presented["observation_id"],
            "public_state_hash": "baseline-public-view",
            "objective": presented["objective"],
            "phase": presented["phase"],
            "state": state,
            "available_action_types": presented["available_action_types"],
            "action_constraints": presented["action_constraints"],
            "recent_public_events": [],
            "memory": "",
            "remaining_budget": {"game_actions": 0, "provider_calls": 0},
        }
    )


class Baseline:
    interface = "tools_v7"
    actor = "agent"
    model = None

    def __init__(self, name="random_legal", seed=0):
        if name not in ("random_legal", "heuristic"):
            raise ValueError("UNKNOWN_BASELINE")
        self.name = name
        self.rng = random.Random(seed)

    def decide(self, ctx, exchanges):
        # Diagnostic transports can round-trip only the user-message portion.
        obs = baseline_observation(
            ctx.observation if isinstance(ctx, Context) else ctx.get("observation")
        )
        choices = list(candidates(obs))
        if not choices:
            return {"kind": "abort", "reason": "NO_PUBLIC_LEGAL_ACTION_FOUND"}
        if self.name == "random_legal":
            chosen = self.rng.choice(choices)
        else:
            # Prefer play; maximize repeated visible ranks, then visible ranks' sum.
            ranks = {card.id: card.rank for card in obs.state.hand}
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
                action = env.action
                ids = getattr(action, "card_ids", [])
                repeats = sum(
                    ranks.get(i) == ranks.get(j) and ranks.get(i) is not None
                    for i, j in combinations(ids, 2)
                )
                return (
                    priority.get(action.type, 0),
                    repeats,
                    sum(values.get(ranks.get(i), 0) for i in ids),
                )

            chosen = max(choices, key=key)
        return {"kind": "action", "envelope": chosen.model_dump(mode="json")}

    def on_decision_end(self) -> None:
        # Baselines retain no provider continuation between actions.
        pass

    def on_commit(self) -> None:
        # The next choice is computed from the next public observation.
        pass


class ScriptedPolicy:
    interface = "tools_v7"
    name = "model"
    actor = "agent"
    model = None

    def __init__(self, operations):
        self.operations = iter(operations)

    def decide(self, ctx, exchanges):
        return next(self.operations)

    def on_decision_end(self) -> None:
        # The script iterator already tracks the next operation.
        pass

    def on_commit(self) -> None:
        # Consuming the operation is the only scripted state change.
        pass
