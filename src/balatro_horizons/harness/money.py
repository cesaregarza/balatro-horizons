"""Durable reservations, funding stops, and their shared refusal vocabulary."""

import json
import math
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from balatro_horizons.storage.journal import atomic_json, identifier, locked, now

# Keep refusal classification in one place.  ``campaign`` identifies reasons
# that can create an immutable scheduling stop; ``outcome`` is the public
# terminal outcome for an episode that raises the refusal.
REFUSAL_OUTCOMES = {
    "EPISODE_COST_CAP": {"outcome": "BUDGET_EXHAUSTED", "campaign": False},
    "CAMPAIGN_COST_CAP": {"outcome": "CAMPAIGN_INTERRUPTED", "campaign": True},
    "EPISODE_AND_CAMPAIGN_COST_CAP": {"outcome": "BUDGET_EXHAUSTED", "campaign": True},
}


class BudgetExhausted(RuntimeError):
    def __init__(self, reason, *, cost_context=None):
        super().__init__(reason)
        self.cost_context = cost_context
        self.outcome = REFUSAL_OUTCOMES.get(reason, {}).get("outcome", "BUDGET_EXHAUSTED")


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

    @classmethod
    def episode_only(cls, path, cap):
        """Construct a standalone ledger whose cap is the campaign ceiling."""
        return cls(path, cap)

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
                raise self._refusal(
                    episode_refused,
                    campaign_refused,
                    context,
                    episode_cap,
                    episode,
                )
            if request_id in entries:
                raise ValueError("DUPLICATE_RESERVATION")
            entries[request_id] = self._entry(episode_id, amount)
            atomic_json(self.path, entries)

    @staticmethod
    def _entry(episode_id, amount):
        return {"episode_id": episode_id, "cost": amount, "reserved": amount, "settled": False}

    @staticmethod
    def _refusal(episode_refused, campaign_refused, context, episode_cap, episode):
        reason = (
            "EPISODE_AND_CAMPAIGN_COST_CAP"
            if episode_refused and campaign_refused
            else "EPISODE_COST_CAP"
            if episode_refused
            else "CAMPAIGN_COST_CAP"
        )
        return BudgetExhausted(
            reason,
            cost_context={
                **context,
                "episode_cap_usd": episode_cap,
                "episode_committed_usd": episode,
            },
        )

    def retain(self, request_id):
        """Keep an unknown-usage reservation explicitly after provider failure."""
        with locked(self.path.with_suffix(".lock")):
            entries = self._entries()
            if request_id not in entries:
                raise KeyError(request_id)
            if entries[request_id]["settled"]:
                raise ValueError("RESERVATION_ALREADY_SETTLED")
            return entries[request_id]["cost"]

    def settle(self, request_id, actual):
        with locked(self.path.with_suffix(".lock")):
            entries = json.loads(self.path.read_text())
            entries[request_id]["cost"] = actual
            entries[request_id]["settled"] = True
            atomic_json(self.path, entries)


class PublicCostContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    campaign_cap_usd: float = Field(gt=0)
    campaign_committed_usd: float = Field(ge=0)
    campaign_headroom_usd: float
    required_usd: float = Field(ge=0)
    unsettled_usd: float = Field(ge=0)
    episode_cap_usd: float | None = Field(default=None, gt=0)
    episode_committed_usd: float | None = Field(default=None, ge=0)


class TerminalReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sequence: int = Field(ge=0)
    timestamp: str


class SchedulingStop(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = "1.0"
    batch_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    recorded_at: str
    reason: Literal[
        "CAMPAIGN_COST_CAP",
        "EPISODE_AND_CAMPAIGN_COST_CAP",
    ]
    stage: Literal["preflight", "episode"]
    slot_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    agent: str
    cost_context: PublicCostContext
    episode_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    outcome: Literal["CAMPAIGN_INTERRUPTED", "BUDGET_EXHAUSTED"] | None = None
    terminal: TerminalReference | None = None
    recovered: bool = False

    @model_validator(mode="after")
    def consistent_stage(self):
        refusal = REFUSAL_OUTCOMES.get(self.reason)
        if refusal is None:
            raise ValueError("UNKNOWN_REFUSAL_REASON")
        if self.stage == "preflight":
            if self.reason != "CAMPAIGN_COST_CAP" or self.episode_id is not None or self.outcome is not None:
                raise ValueError("INVALID_PREFLIGHT_STOP")
            if self.terminal is not None or self.recovered:
                raise ValueError("INVALID_PREFLIGHT_STOP")
        elif (
            not refusal["campaign"]
            or not self.episode_id
            or self.terminal is None
            or self.outcome != refusal["outcome"]
        ):
            raise ValueError("INVALID_EPISODE_STOP")
        return self


def batch_attempts(store, plan):
    """Read original attempts from manifests and hash-checked terminal journals."""
    slots = {slot["slot_id"]: slot for slot in plan["slots"]}
    rows = []
    for path in sorted((store.root / "public_runs").glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        if not _is_batch_attempt(manifest, plan, slots):
            continue
        if manifest["agent"] != slots[manifest["slot_id"]]["agent"]:
            raise ValueError("BATCH_ATTEMPT_AGENT_MISMATCH")
        rows.append(_attempt(store, path, manifest))
    return sorted(rows, key=lambda row: (row["manifest"]["created_at"], row["episode_id"]))


def _is_batch_attempt(manifest, plan, slots):
    return (
        manifest.get("batch_id") == plan["batch_id"]
        and not manifest.get("parent_episode_id")
        and not manifest.get("assistance")
        and manifest.get("agent") != "human"
        and manifest.get("slot_id") in slots
    )


def _attempt(store, path, manifest):
    eid = identifier(path.parent.name)
    with locked(path.parent / ".writer.lock"):
        terminal = next((event for event in reversed(store.events(eid)) if event["type"] == "terminal"), None)
    summary = (
        {**terminal["payload"], "terminal_event_id": terminal["event_id"], "journal_head": terminal["hash"]}
        if terminal
        else None
    )
    return {"episode_id": eid, "manifest": manifest, "summary": summary, "terminal": terminal}


def _validate_stop(value, plan):
    stop = SchedulingStop.model_validate(value).model_dump(mode="json", exclude_none=True)
    if stop["batch_id"] != plan["batch_id"] or not any(
        slot["slot_id"] == stop["slot_id"] and slot["agent"] == stop["agent"]
        for slot in plan["slots"]
    ):
        raise ValueError("BATCH_STOP_PLAN_MISMATCH")
    return stop


def _read_stop(path, plan):
    try:
        content = path.read_text()
    except FileNotFoundError:
        return None
    return _validate_stop(json.loads(content), plan)


def _stop_path(store, plan):
    return store.root / "batches" / identifier(plan["batch_id"])


def read_stop(store, plan):
    directory = _stop_path(store, plan)
    with locked(directory / "stop.lock"):
        return _read_stop(directory / "stop.json", plan)


def record_stop(store, plan, **fields):
    directory = _stop_path(store, plan)
    with locked(directory / "stop.lock"):
        existing = _read_stop(directory / "stop.json", plan)
        if existing is not None:
            return existing
        value = _validate_stop({"batch_id": plan["batch_id"], "recorded_at": _now(), **fields}, plan)
        atomic_json(directory / "stop.json", value)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return value


def _now():
    return now()


def reconcile_stop(store, plan, attempts, *, recovered=True):
    existing = read_stop(store, plan)
    if existing is not None:
        return existing
    candidates = [
        row
        for row in attempts
        if REFUSAL_OUTCOMES.get((row["summary"] or {}).get("reason"), {}).get("campaign")
    ]
    if not candidates:
        return None
    row = min(candidates, key=lambda item: (item["terminal"]["timestamp"], item["episode_id"]))
    terminal, summary, manifest = row["terminal"], row["summary"], row["manifest"]
    return record_stop(
        store,
        plan,
        reason=summary["reason"],
        stage="episode",
        slot_id=manifest["slot_id"],
        agent=manifest["agent"],
        episode_id=row["episode_id"],
        outcome=summary["outcome"],
        cost_context=summary["cost_context"],
        recovered=recovered,
        terminal={key: terminal[key] for key in ("event_id", "hash", "sequence", "timestamp")},
    )
