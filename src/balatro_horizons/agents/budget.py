"""Durable conservative reservations, shared across all attempts in a batch."""

import json
from pathlib import Path

from balatro_horizons.storage.journal import atomic_json, locked


class BudgetExhausted(RuntimeError):
    pass


class Spending:
    def __init__(self, path, cap):
        self.path = Path(path)
        self.cap = cap

    def reserve(self, request_id, episode_id, amount, episode_cap):
        with locked(self.path.with_suffix(".lock")):
            entries = json.loads(self.path.read_text()) if self.path.exists() else {}
            total = sum(e["cost"] for e in entries.values())
            episode = sum(e["cost"] for e in entries.values() if e["episode_id"] == episode_id)
            if (
                self.cap is None
                or episode_cap is None
                or total + amount > self.cap
                or episode + amount > episode_cap
            ):
                raise BudgetExhausted("COST_CAP_REACHED")
            if request_id in entries:
                raise ValueError("DUPLICATE_RESERVATION")
            entries[request_id] = {
                "episode_id": episode_id,
                "cost": amount,
                "reserved": amount,
                "settled": False,
            }
            atomic_json(self.path, entries)

    def settle(self, request_id, actual):
        with locked(self.path.with_suffix(".lock")):
            entries = json.loads(self.path.read_text())
            entries[request_id]["cost"] = actual
            entries[request_id]["settled"] = True
            atomic_json(self.path, entries)
