"""Allowlisted projection from private adapter state to the model boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from balatro_horizons.contracts import (
    DeckKnowledge,
    Objective,
    Observation,
    Progress,
    PublicBlind,
    PublicCard,
    PublicOffer,
    PublicState,
    RecentPublicEvent,
    RemainingBudget,
    Resources,
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class HandleIssuer:
    """Issue by public encounter order, never hash native identity into a handle."""

    def __init__(self, salt: bytes | None = None):
        self._salt = salt or secrets.token_bytes(32)
        self._stable = {}
        self._current = {}
        self._serial = 0

    def begin_observation(self, observation_id):
        self._current = {}

    def issue(self, area, native_id, *, trackable=True):
        key = (area, native_id)
        if not trackable:
            self._stable.pop(key, None)
        handle = self._stable.get(key) if trackable else None
        if handle is None:
            self._serial += 1
            handle = (
                "h_"
                + hmac.new(self._salt, str(self._serial).encode(), hashlib.sha256).hexdigest()[:20]
            )
            if trackable:
                self._stable[key] = handle
        self._current[handle] = key
        return handle

    def end_observation(self):
        present = set(self._current.values())
        self._stable = {k: v for k, v in self._stable.items() if k in present}

    def resolve_current(self, handle, area):
        target = self._current.get(handle)
        if target is None or target[0] != area:
            raise ValueError("UNKNOWN_PUBLIC_HANDLE")
        return target[1]

    def snapshot(self):
        return {
            "salt": self._salt.hex(),
            "serial": self._serial,
            "stable": [[*k, v] for k, v in self._stable.items()],
            "current": [[h, *k] for h, k in self._current.items()],
        }

    @classmethod
    def restore(cls, snapshot):
        result = cls(bytes.fromhex(snapshot["salt"]))
        result._serial = snapshot["serial"]
        result._stable = {(a, n): h for a, n, h in snapshot["stable"]}
        result._current = {h: (a, n) for h, a, n in snapshot["current"]}
        return result


def _card(raw: dict[str, Any], area: str, issuer: HandleIssuer) -> PublicCard:
    face_down = bool(raw.get("face_down", False))
    handle = issuer.issue(
        area, str(raw["native_id"]), trackable=bool(raw.get("trackable", True)) and not face_down
    )
    return PublicCard(
        id=handle,
        label="Face-down card" if face_down else str(raw["label"]),
        face_down=face_down,
        rank=None if face_down else (None if raw.get("rank") is None else str(raw["rank"])),
        suit=None if face_down else (None if raw.get("suit") is None else str(raw["suit"])),
        effects=[] if face_down else [str(item) for item in raw.get("effects", [])],
        counters={} if face_down else {str(k): str(v) for k, v in raw.get("counters", {}).items()},
        sell_price=None if face_down or raw.get("sell_price") is None else str(raw["sell_price"]),
        sellable=None if face_down else bool(raw.get("sellable", False)),
        usable=bool(raw.get("usable", False)) and not face_down,
        min_targets=0 if face_down else int(raw.get("min_targets", 0)),
        max_targets=0 if face_down else int(raw.get("max_targets", 0)),
    )


def project_public(
    raw: dict[str, Any],
    *,
    episode_id: str,
    observation_id: int,
    issuer: HandleIssuer,
    memory: str,
    remaining_budget: RemainingBudget,
    recent_events: list[RecentPublicEvent] | None = None,
) -> Observation:
    """Construct every public field explicitly; unknown raw keys never cross."""
    issuer.begin_observation(observation_id)
    visible = raw["visible"]
    progress = visible["progress"]
    resources = visible["resources"]
    state = PublicState(
        progress=Progress(
            ante=progress.get("ante"),
            blind=progress.get("blind"),
            native_status=progress.get("native_status"),
            round_number=progress.get("round_number"),
        ),
        resources=Resources(
            money=None if resources.get("money") is None else str(resources["money"]),
            hands=resources.get("hands"),
            discards=resources.get("discards"),
            chips=None if resources.get("chips") is None else str(resources["chips"]),
            target=None if resources.get("target") is None else str(resources["target"]),
            joker_capacity=resources.get("joker_capacity"),
            consumable_capacity=resources.get("consumable_capacity"),
            credit_limit=str(resources.get("credit_limit", 0)),
            shop_reroll_cost=None
            if resources.get("shop_reroll_cost") is None
            else str(resources["shop_reroll_cost"]),
            boss_reroll_cost=None
            if resources.get("boss_reroll_cost") is None
            else str(resources["boss_reroll_cost"]),
            pack_choices_remaining=resources.get("pack_choices_remaining"),
        ),
        hand=[_card(card, "hand", issuer) for card in visible.get("hand", [])],
        jokers=[_card(card, "jokers", issuer) for card in visible.get("jokers", [])],
        consumables=[_card(card, "consumables", issuer) for card in visible.get("consumables", [])],
        revealed_blinds=[
            PublicBlind(
                id=issuer.issue("blinds", str(blind["native_id"])),
                label=str(blind["label"]),
                kind=str(blind["kind"]),
                target=str(blind["target"]),
                skip_allowed=bool(blind.get("skip_allowed", False)),
                effects=[str(effect) for effect in blind.get("effects", [])],
            )
            for blind in visible.get("revealed_blinds", [])
        ],
        offers=[
            PublicOffer(
                id=issuer.issue("offers", str(offer["native_id"])),
                label=str(offer["label"]),
                kind=str(offer["kind"]),
                price=str(offer["price"]),
                acquire_allowed=bool(offer.get("acquire_allowed", True)),
                buy_and_use_allowed=bool(offer.get("buy_and_use_allowed", False)),
                min_targets=int(offer.get("min_targets", 0)),
                max_targets=int(offer.get("max_targets", 0)),
                effects=[str(effect) for effect in offer.get("effects", [])],
            )
            for offer in visible.get("offers", [])
        ],
        public_deck_knowledge=DeckKnowledge(
            initial_count=visible.get("deck_knowledge", {}).get("initial_count"),
            observed_draws=visible.get("deck_knowledge", {}).get("observed_draws"),
            composition={
                str(k): int(v)
                for k, v in visible.get("deck_knowledge", {}).get("composition", {}).items()
            },
            remaining_exact=visible.get("deck_knowledge", {}).get("remaining_exact"),
            provenance=visible.get("deck_knowledge", {}).get("provenance", "unknown"),
        ),
        hand_levels={str(k): str(v) for k, v in visible.get("hand_levels", {}).items()},
        persistent_effects=[str(effect) for effect in visible.get("persistent_effects", [])],
    )
    action_types = [str(kind) for kind in visible["available_action_types"]]
    constraints = _constraints(action_types, state)
    if "reorder" in constraints:
        areas = constraints["reorder"]["areas"]
        constraints["reorder"]["areas"] = [
            area for area in areas if area in visible.get("reorder_areas", areas)
        ]
        if not constraints["reorder"]["areas"]:
            del constraints["reorder"]
            action_types.remove("reorder")
    if "play_hand" in constraints:
        constraints["play_hand"]["max_cards"] = min(
            int(visible.get("max_play_cards", 5)), len(state.hand)
        )
        constraints["play_hand"]["required_ids"] = [
            card.id
            for card, private in zip(state.hand, visible.get("hand", []), strict=True)
            if private.get("forced_selection")
        ]
    if "discard" in constraints and "play_hand" in constraints:
        constraints["discard"]["required_ids"] = constraints["play_hand"]["required_ids"]
    issuer.end_observation()
    objective = Objective()
    hashed = canonical_json(
        {
            "objective": objective.model_dump(mode="json"),
            "phase": str(visible["phase"]),
            "state": state.model_dump(mode="json"),
            "available_action_types": action_types,
            "action_constraints": constraints,
        }
    )
    return Observation(
        episode_id=episode_id,
        observation_id=observation_id,
        public_state_hash="sha256:" + hashlib.sha256(hashed.encode()).hexdigest(),
        objective=objective,
        phase=str(visible["phase"]),
        state=state,
        available_action_types=action_types,
        action_constraints=constraints,
        recent_public_events=recent_events or [],
        memory=memory,
        remaining_budget=remaining_budget,
    )


def _constraints(
    action_types: list[str], state: PublicState
) -> dict[str, dict[str, int | str | bool | list[str]]]:
    constraints: dict[str, dict[str, int | str | bool | list[str]]] = {}
    if "play_hand" in action_types:
        constraints["play_hand"] = {"min_cards": 1, "max_cards": min(5, len(state.hand))}
    if "discard" in action_types:
        constraints["discard"] = {"min_cards": 1, "max_cards": min(5, len(state.hand))}
    if "reorder" in action_types:
        constraints["reorder"] = {
            "areas": [
                area for area in ("hand", "jokers", "consumables") if len(getattr(state, area)) > 1
            ],
            "complete_permutation": True,
        }
    if "buy" in action_types:
        constraints["buy"] = {"visible_affordability": True}
    return constraints
