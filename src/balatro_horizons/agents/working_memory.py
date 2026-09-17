"""Bounded, public working history; no generated summaries or opaque provider state."""

import json
from copy import deepcopy

from balatro_horizons.config import (
    RETAINED_HELPER_RESULTS,
    WORKING_MEMORY_BYTES,
    WORKING_MEMORY_DECISIONS,
    WORKING_MEMORY_HELPER_BYTES,
)
from balatro_horizons.storage.journal import digest

VERSION = "working-memory-v1"


def policy():
    return {"version": VERSION, "max_decisions": WORKING_MEMORY_DECISIONS,
            "max_bytes": WORKING_MEMORY_BYTES, "helpers_per_decision": RETAINED_HELPER_RESULTS,
            "max_helper_bytes": WORKING_MEMORY_HELPER_BYTES}


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


class WorkingMemory:
    """Fold only public observations, committed actions and normalized helper receipts.

    The journal remains authoritative. Frames contain historical handles/prices,
    never executable tool turns, and are explicitly labelled as past information.
    """

    def __init__(self):
        self.frames = []
        self.omitted = 0
        self.helpers = []
        self.omitted_helpers = 0
        self.observation = None
        self.pending = None

    def observe(self, observation):
        if self.pending is not None:
            self.pending["observed_result"] = deepcopy(observation.get("last_action"))
            self.frames.append(self.pending)
            self.pending = None
            self._prune()
        self.observation = observation

    def consume(self, event):
        kind, payload = event["type"], event["payload"]
        if kind == "observation":
            self.observe(payload)
        elif kind == "helper_result":
            operation = payload["operation"]
            # Note acknowledgments are redundant with the always-delivered notebook.
            if operation.get("kind") in ("set_run_note", "delete_run_note"):
                return
            record = {"episode_id": event["episode_id"], "event_id": event["event_id"],
                      "operation": deepcopy(operation), "result": deepcopy(payload["result"])}
            if size(record) > WORKING_MEMORY_HELPER_BYTES:
                self.omitted_helpers += 1
                return
            self.helpers.append(record)
            if len(self.helpers) > RETAINED_HELPER_RESULTS:
                self.helpers.pop(0)
                self.omitted_helpers += 1
        elif kind == "action_commit":
            before = self.observation or {}
            state = before.get("state", {})
            self.pending = {
                "episode_id": event["episode_id"], "decision_id": event["observation_id"],
                "action_event_id": event["event_id"], "phase": before.get("phase"),
                "progress": deepcopy(state.get("progress")),
                "resources_before": deepcopy(state.get("resources")),
                "action": deepcopy(payload["action"]),
                "recorded_decision_note": payload.get("decision_note"),
                "helpers": self.helpers, "omitted_helpers": self.omitted_helpers,
            }
            self.helpers, self.omitted_helpers = [], 0

    def _prune(self):
        while self.frames and (len(self.frames) > WORKING_MEMORY_DECISIONS
                               or size(self.frames) > WORKING_MEMORY_BYTES):
            self.frames.pop(0)
            self.omitted += 1

    def view(self):
        return {**policy(), "frames": deepcopy(self.frames), "omitted_decisions": self.omitted,
                "meaning": "Historical public records, not current state or verified agent conclusions."}


def restore_working_memory(snapshot, prefix, observation):
    memory = WorkingMemory()
    for event in prefix:
        memory.consume(event)
    # Branch prefixes end immediately before their boundary observation.
    memory.observe(observation)
    if digest(memory.view()) != digest(snapshot):
        raise ValueError("WORKING_MEMORY_SNAPSHOT_MISMATCH")
    return memory


def trim_oldest(ctx):
    memory = ctx.get("working_memory")
    if not memory or not memory["frames"]:
        return False
    memory["frames"].pop(0)
    memory["omitted_decisions"] += 1
    memory["request_pruned_decisions"] = memory.get("request_pruned_decisions", 0) + 1
    return True


def maintenance(ctx, loaded_results):
    """Give advance notice while retained information is still available to save."""
    memory = ctx["working_memory"]
    frames = memory["frames"]
    imminent = frames[:1] if len(frames) >= WORKING_MEMORY_DECISIONS else []
    ctx["notebook_maintenance"] = {
        "oldest_decision_leaves_after_action": (
            {k: imminent[0][k] for k in ("episode_id", "decision_id")} if imminent else None
        ),
        "next_helper_may_clear_older_results": loaded_results >= RETAINED_HELPER_RESULTS,
        "message": (
            "Preserve useful conclusions before acting or loading more information clears older context. "
            "Update changed notes; unchanged notes need no write. note_update can accompany your action."
        ),
    }
