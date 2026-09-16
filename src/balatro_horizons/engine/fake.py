"""Scripted transport fake; it does not implement Balatro rules or strategy."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from balatro_horizons.contracts import Action
from balatro_horizons.observations.projection import HandleIssuer


class FakeGame:
    """A small deterministic episode for plumbing and UI tests only."""

    evidence_kind = "SYNTHETIC_TEST"

    def __init__(self, private_seed: str = "offline-fixture-1") -> None:
        self.private_seed = private_seed
        self.phase = "BLIND_SELECT"
        self.discards = 1
        self.money = 4
        self.bought = False
        self.committed = 0
        self._terminal: str | None = None
        self._requests = set()

    def observe_private(self) -> dict[str, Any]:
        blind = {
            "native_id": "blind-internal-1",
            "label": "Small Blind",
            "kind": "SMALL",
            "target": "10",
            "skip_allowed": True,
            "effects": [],
            "hidden_future_boss": "forbidden-boss",
        }
        hand = [
            {
                "native_id": "card-internal-a",
                "label": "Ace of Hearts",
                "rank": "A",
                "suit": "Hearts",
                "secret_draw_position": 39,
            },
            {
                "native_id": "card-internal-b",
                "label": "Ace of Spades",
                "rank": "A",
                "suit": "Spades",
                "secret_draw_position": 7,
            },
            {
                "native_id": "card-internal-c",
                "label": "Two of Clubs",
                "rank": "2",
                "suit": "Clubs",
                "secret_draw_position": 11,
            },
        ]
        visible = {
            "phase": self.phase,
            "progress": {"ante": 1, "blind": "Small Blind", "native_status": self.phase},
            "resources": {
                "money": self.money,
                "hands": 4 if self.phase == "SELECTING_HAND" else 0,
                "discards": self.discards if self.phase == "SELECTING_HAND" else 0,
                "chips": "12" if self.phase in {"ROUND_EVAL", "SHOP", "GAME_OVER"} else "0",
                "target": "10",
                "joker_capacity": 5,
                "consumable_capacity": 2,
            },
            "hand": hand if self.phase == "SELECTING_HAND" else [],
            "jokers": [],
            "consumables": [],
            "revealed_blinds": [blind] if self.phase == "BLIND_SELECT" else [],
            "offers": [
                {
                    "native_id": "offer-internal-1",
                    "label": "Test Joker",
                    "kind": "joker",
                    "price": 4,
                }
            ]
            if self.phase == "SHOP" and not self.bought
            else [],
            "deck_knowledge": {
                "initial_count": 52,
                "observed_draws": 3,
                "remaining_exact": None,
                "provenance": "public_history",
            },
            "hand_levels": {"Pair": "1"},
            "persistent_effects": [],
            "available_action_types": self._available_actions(),
            "internal_queue": ["forbidden-pending-effect"],
        }
        return deepcopy(
            {
                "seed": self.private_seed,
                "rng_state": "forbidden-rng-state",
                "hidden_draw_order": ["forbidden-first-card"],
                "visible": visible,
            }
        )

    def _available_actions(self) -> list[str]:
        if self.phase == "BLIND_SELECT":
            return ["select_blind", "skip_blind"]
        if self.phase == "SELECTING_HAND":
            return ["play_hand", "discard"] if self.discards else ["play_hand"]
        if self.phase == "ROUND_EVAL":
            return ["cash_out"]
        if self.phase == "SHOP":
            return ["buy", "leave_shop"] if not self.bought else ["leave_shop"]
        return []

    def wait_ready(self) -> None:
        return None

    def apply_public_action(self, action: Action, issuer: HandleIssuer, request_id=None) -> None:
        """Act once. The real adapter must resolve native indices here."""
        if request_id in self._requests:
            return
        if action.type not in self._available_actions():
            raise ValueError("Action unavailable at fake decision boundary")
        if action.type in {"select_blind", "skip_blind"}:
            issuer.resolve_current(action.blind_id, "blinds")
            self.phase = "SELECTING_HAND" if action.type == "select_blind" else "SHOP"
        elif action.type in {"play_hand", "discard"}:
            for handle in action.card_ids:
                issuer.resolve_current(handle, "hand")
            if action.type == "discard":
                self.discards -= 1
            else:
                self.phase = "ROUND_EVAL"
        elif action.type == "cash_out":
            self.money += 4
            self.phase = "SHOP"
        elif action.type == "buy":
            issuer.resolve_current(action.offer_id, "offers")
            self.money -= 4
            self.bought = True
        elif action.type == "leave_shop":
            self.phase = "GAME_OVER"
            self._terminal = "WIN"
        else:
            raise ValueError("Unsupported fake action")
        self.committed += 1
        if request_id:
            self._requests.add(request_id)

    def terminal_status(self) -> str | None:
        return self._terminal

    def checkpoint(self):
        state = deepcopy(self.__dict__)
        state["_requests"] = sorted(state["_requests"])
        return {"kind": "synthetic", "state": state}

    def restore(self, snapshot):
        self.__dict__ = deepcopy(snapshot["state"])
        self._requests = set(self._requests)

    def close(self):
        pass
