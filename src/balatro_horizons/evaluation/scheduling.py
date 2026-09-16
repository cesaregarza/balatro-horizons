"""Original batch attempts and immutable funding stops, independent of the index."""

import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from balatro_horizons.agents.budget import CAMPAIGN_REASONS
from balatro_horizons.storage.journal import atomic_json, identifier, locked, now


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
    reason: Literal["CAMPAIGN_COST_CAP", "EPISODE_AND_CAMPAIGN_COST_CAP"]
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
        if self.stage == "preflight":
            if (
                self.reason != "CAMPAIGN_COST_CAP"
                or self.episode_id is not None
                or self.outcome is not None
                or self.terminal is not None
                or self.recovered
            ):
                raise ValueError("INVALID_PREFLIGHT_STOP")
        else:
            expected = (
                "CAMPAIGN_INTERRUPTED" if self.reason == "CAMPAIGN_COST_CAP" else "BUDGET_EXHAUSTED"
            )
            if not self.episode_id or self.terminal is None or self.outcome != expected:
                raise ValueError("INVALID_EPISODE_STOP")
        return self


def batch_attempts(store, plan):
    """Read original attempts from manifests and hash-checked terminal journals.

    SQLite can miss a terminal (or the entire attempt) after a crash. Assisted
    children can inherit batch metadata but never resolve or stop its scheduling.
    """
    slots = {s["slot_id"]: s for s in plan["slots"]}
    rows = []
    for path in sorted((store.root / "public_runs").glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        if (
            manifest.get("batch_id") != plan["batch_id"]
            or manifest.get("parent_episode_id")
            or manifest.get("assistance")
            or manifest.get("agent") == "human"
            or manifest.get("slot_id") not in slots
        ):
            continue
        if manifest["agent"] != slots[manifest["slot_id"]]["agent"]:
            raise ValueError("BATCH_ATTEMPT_AGENT_MISMATCH")
        eid = identifier(path.parent.name)
        with locked(path.parent / ".writer.lock"):
            terminal = next(
                (e for e in reversed(store.events(eid)) if e["type"] == "terminal"), None
            )
        summary = (
            {
                **terminal["payload"],
                "terminal_event_id": terminal["event_id"],
                "journal_head": terminal["hash"],
            }
            if terminal
            else None
        )
        rows.append(
            {"episode_id": eid, "manifest": manifest, "summary": summary, "terminal": terminal}
        )
    return sorted(rows, key=lambda row: (row["manifest"]["created_at"], row["episode_id"]))


def _validate_stop(value, plan):
    stop = SchedulingStop.model_validate(value).model_dump(mode="json", exclude_none=True)
    if stop["batch_id"] != plan["batch_id"] or not any(
        s["slot_id"] == stop["slot_id"] and s["agent"] == stop["agent"] for s in plan["slots"]
    ):
        raise ValueError("BATCH_STOP_PLAN_MISMATCH")
    return stop


def _read_stop(path, plan):
    try:
        content = path.read_text()
    except FileNotFoundError:
        return None
    # Corrupt, unreadable, or incompatible records fail closed; only absence is None.
    return _validate_stop(json.loads(content), plan)


def read_stop(store, plan):
    directory = store.root / "batches" / identifier(plan["batch_id"])
    with locked(directory / "stop.lock"):
        return _read_stop(directory / "stop.json", plan)


def record_stop(store, plan, **fields):
    directory = store.root / "batches" / identifier(plan["batch_id"])
    with locked(directory / "stop.lock"):
        existing = _read_stop(directory / "stop.json", plan)
        if existing is not None:
            return existing
        value = _validate_stop({"batch_id": plan["batch_id"], "recorded_at": now(), **fields}, plan)
        # Guarded first-write with atomic visibility. Never replace an existing stop.
        atomic_json(directory / "stop.json", value)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return value


def reconcile_stop(store, plan, attempts, *, recovered=True):
    existing = read_stop(store, plan)
    if existing is not None:
        return existing
    candidates = [
        row for row in attempts if (row["summary"] or {}).get("reason") in CAMPAIGN_REASONS
    ]
    if not candidates:
        return None
    row = min(candidates, key=lambda r: (r["terminal"]["timestamp"], r["episode_id"]))
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
