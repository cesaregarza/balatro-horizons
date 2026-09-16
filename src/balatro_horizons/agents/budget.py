"""Durable conservative reservations, shared across all attempts in a batch."""

import json
import math
from pathlib import Path

from balatro_horizons.storage.journal import atomic_json, locked

CAMPAIGN_REASONS = {"CAMPAIGN_COST_CAP", "EPISODE_AND_CAMPAIGN_COST_CAP"}


class BudgetExhausted(RuntimeError):
    def __init__(self, reason, *, cost_context=None):
        super().__init__(reason)
        self.cost_context = cost_context
        self.outcome = (
            "CAMPAIGN_INTERRUPTED" if reason == "CAMPAIGN_COST_CAP" else "BUDGET_EXHAUSTED"
        )


def reservation_usd(model, limits):
    """The unchanged worst-case reservation, including maximum input pricing."""
    return (
        limits.max_input_tokens_per_call * model.maximum_input_usd_per_million
        + limits.max_output_tokens_per_call * model.output_usd_per_million
    ) / 1_000_000


def can_afford(total, amount, cap):
    # Headroom subtraction is diagnostic only: it rounds differently at the boundary.
    return total + amount <= cap


def validate_caps(episode_cap, campaign_cap):
    if any(
        isinstance(cap, bool)
        or not isinstance(cap, (int, float))
        or not math.isfinite(cap)
        or cap <= 0
        for cap in (episode_cap, campaign_cap)
    ):
        raise ValueError("PAID_EXECUTION_NOT_AUTHORIZED")


def validate_paid_configuration(model, limits):
    if not limits.paid_calls_enabled:
        raise ValueError("PAID_EXECUTION_NOT_AUTHORIZED")
    validate_caps(limits.max_episode_cost_usd, limits.max_batch_cost_usd)
    amount = reservation_usd(model, limits)
    if not can_afford(0, amount, limits.max_episode_cost_usd):
        raise ValueError("EPISODE_CAP_BELOW_RESERVATION")
    return amount


class Spending:
    def __init__(self, path, cap):
        self.path = Path(path)
        self.cap = cap

    def _entries(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _context(self, entries, amount):
        total = sum(e["cost"] for e in entries.values())
        return {
            "campaign_cap_usd": self.cap,
            "campaign_committed_usd": total,
            "campaign_headroom_usd": self.cap - total,
            "required_usd": amount,
            "unsettled_usd": sum(e["cost"] for e in entries.values() if not e["settled"]),
        }

    def affordability(self, amount):
        """Non-binding snapshot. Every actual call must still reserve under the lock."""
        validate_caps(self.cap, self.cap)
        with locked(self.path.with_suffix(".lock")):
            context = self._context(self._entries(), amount)
        return can_afford(context["campaign_committed_usd"], amount, self.cap), context

    def reserve(self, request_id, episode_id, amount, episode_cap, *, prior_cost=0):
        validate_caps(episode_cap, self.cap)
        with locked(self.path.with_suffix(".lock")):
            entries = self._entries()
            context = self._context(entries, amount)
            episode = prior_cost + sum(
                e["cost"] for e in entries.values() if e["episode_id"] == episode_id
            )
            episode_refused = not can_afford(episode, amount, episode_cap)
            campaign_refused = not can_afford(context["campaign_committed_usd"], amount, self.cap)
            if episode_refused or campaign_refused:
                reason = (
                    "EPISODE_AND_CAMPAIGN_COST_CAP"
                    if episode_refused and campaign_refused
                    else "EPISODE_COST_CAP"
                    if episode_refused
                    else "CAMPAIGN_COST_CAP"
                )
                raise BudgetExhausted(
                    reason,
                    cost_context={
                        **context,
                        "episode_cap_usd": episode_cap,
                        "episode_committed_usd": episode,
                    },
                )
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
