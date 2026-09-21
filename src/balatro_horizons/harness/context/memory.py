"""Agent-authored notes and public working history; journals remain authoritative."""

import json
from copy import deepcopy

from balatro_horizons.config import (
    MAX_MEMORY_CHARACTERS,
    NOTEBOOK_KEY_MAX,
    NOTEBOOK_KEY_MIN,
    RETAINED_HELPER_RESULTS,
    WORKING_MEMORY_BYTES,
    WORKING_MEMORY_DECISIONS,
    WORKING_MEMORY_HELPER_BYTES,
)
from balatro_horizons.storage.journal import digest

VERSION = "run-notebook-v1"
MAX_KEY_CHARACTERS = NOTEBOOK_KEY_MAX


def notebook_tools(definitions, *, action_notes=False):
    from balatro_horizons.agents.tool_interface import ACTION_MODELS, tool

    result = deepcopy(definitions)
    for definition in result:
        if definition["name"] in ACTION_MODELS:
            schema = definition["parameters"]
            schema["properties"].pop("memory_update", None)
            schema["required"].remove("memory_update")
            if action_notes:
                schema["properties"]["note_update"] = {
                    "anyOf": [{"type": "null"}, {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "key": {"type": "string", "minLength": NOTEBOOK_KEY_MIN,
                                    "maxLength": MAX_KEY_CHARACTERS},
                            "text": {"type": ["string", "null"],
                                     "maxLength": MAX_MEMORY_CHARACTERS},
                        }, "required": ["key", "text"],
                    }],
                    "description": "One notebook edit saved BEFORE this action executes: null keeps notes unchanged; {key,text} sets a note; text:null deletes it. Write intentions or predictions, not unobserved success. Invalid edits reject the action too. No helper call is charged. The saved edit survives execution failure. On the next decision, previous_action_outcome pairs the recorded edit with observed results when retained; reconcile it then.",
                }
                schema["required"].append("note_update")
    key = {"type": "string", "minLength": NOTEBOOK_KEY_MIN, "maxLength": MAX_KEY_CHARACTERS}
    return result + [
        tool("set_run_note", "Create or replace one agent-authored run note. Counts against the helper allowance; does not advance the game.",
             {"key": key, "text": {"type": "string", "maxLength": MAX_MEMORY_CHARACTERS}}),
        tool("delete_run_note", "Delete one run note by key. Other notes remain unchanged. Does not advance the game.", {"key": key}),
        tool("retrieve_action_result", "Read one committed gameplay action and its observed result. Null decision_id selects the latest. Use episode_id to disambiguate inherited decisions, otherwise null. Receipt separates the recorded note from observed changes; before/after contain public conditions. UTF-8 pages; follow next_offset as byte_offset.",
             {"decision_id": {"type": ["integer", "null"], "minimum": 0},
              "episode_id": {"type": ["string", "null"], "pattern": "^[a-f0-9]{32}$"},
              "section": {"type": "string", "enum": ["receipt", "before", "after"]},
              "byte_offset": {"type": "integer", "minimum": 0}}),
    ]


def used_characters(entries):
    # Python len counts Unicode code points. Keys and text count; fixed metadata does not.
    return sum(len(key) + len(text) for key, text in entries.items())


def valid_key(key):
    return (
        isinstance(key, str) and NOTEBOOK_KEY_MIN <= len(key) <= MAX_KEY_CHARACTERS
        and bool(key.strip())
        and not any(ord(c) < 32 or ord(c) == 127 for c in key)
    )


class RunNotebook:
    def __init__(self, limit=MAX_MEMORY_CHARACTERS):
        self.limit = limit
        self.entries = {}
        self.revision = 0

    def snapshot(self):
        return {"version": VERSION, "revision": self.revision,
                "entries": deepcopy(self.entries), "content_hash": digest(self.entries)}

    def view(self):
        return {**self.snapshot(), "author": "agent", "character_limit": self.limit,
                "used_characters": used_characters(self.entries)}

    def propose(self, kind, key, text=None):
        if not valid_key(key):
            return None, {"error": "INVALID_RUN_NOTE_KEY", "max_key_characters": MAX_KEY_CHARACTERS}
        entries = deepcopy(self.entries)
        if kind == "set_run_note" and isinstance(text, str):
            entries[key] = text
        elif kind == "delete_run_note":
            if key not in entries:
                return None, {"error": "RUN_NOTE_NOT_FOUND", "key": key}
            del entries[key]
        else:
            return None, {"error": "INVALID_RUN_NOTE_OPERATION"}
        size = used_characters(entries)
        if size > self.limit:
            return None, {"error": "RUN_NOTEBOOK_LIMIT", "character_limit": self.limit,
                          "proposed_characters": size, "used_characters": used_characters(self.entries),
                          "message": "Shorten or delete notes; existing notes have not changed."}
        event = {"version": VERSION, "previous_revision": self.revision,
                 "revision": self.revision + 1, "operation": kind, "key": key,
                 "content_hash": digest(entries)}
        if kind == "set_run_note":
            event["text"] = text
        return event, {"revision": self.revision + 1, "used_characters": size,
                       "character_limit": self.limit}

    def apply(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("RUN_NOTEBOOK_JOURNAL_MISMATCH")
        expected, _ = self.propose(payload.get("operation"), payload.get("key"),
                                       payload.get("text"))
        if expected is None or digest(payload) != digest(expected):
            raise ValueError("RUN_NOTEBOOK_JOURNAL_MISMATCH")
        if payload["operation"] == "set_run_note":
            self.entries[payload["key"]] = payload["text"]
        else:
            del self.entries[payload["key"]]
        self.revision = payload["revision"]


def fold_notebook(events, limit=MAX_MEMORY_CHARACTERS):
    notebook = RunNotebook(limit)
    for event in events:
        if event["type"] == "run_note":
            if event.get("actor") != "agent":
                raise ValueError("RUN_NOTEBOOK_JOURNAL_MISMATCH")
            notebook.apply(event["payload"])
    return notebook


def restore_notebook(snapshot, prefix, limit=MAX_MEMORY_CHARACTERS):
    notebook = fold_notebook(prefix, limit)
    if digest(snapshot) != digest(notebook.snapshot()):
        raise ValueError("RUN_NOTEBOOK_SNAPSHOT_MISMATCH")
    return notebook


WORKING_MEMORY_VERSION = "working-memory-v2"


def working_memory_policy():
    return {"version": WORKING_MEMORY_VERSION, "max_decisions": WORKING_MEMORY_DECISIONS,
            "max_bytes": WORKING_MEMORY_BYTES, "helpers_per_decision": RETAINED_HELPER_RESULTS,
            "max_helper_bytes": WORKING_MEMORY_HELPER_BYTES}


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


class WorkingMemory:
    """Fold public observations, actions, linked notebook edits and helper receipts."""

    def __init__(self):
        from balatro_horizons.agents.action_notes import ActionNoteLink

        self.frames = []
        self.omitted = 0
        self.helpers = []
        self.omitted_helpers = 0
        self.observation = None
        self.pending = None
        self.action_notes = ActionNoteLink()

    def observe(self, observation):
        if self.pending is not None:
            self.pending["observed_result"] = deepcopy(observation.get("last_action"))
            self.frames.append(self.pending)
            self.pending = None
            self._prune()
        self.observation = observation

    def consume(self, event):
        note_update = self.action_notes.consume(event)
        kind, payload = event["type"], event["payload"]
        if kind == "observation":
            self.observe(payload)
        elif kind == "helper_result":
            operation = payload["operation"]
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
                "recorded_note_update": note_update,
                "helpers": self.helpers, "omitted_helpers": self.omitted_helpers,
            }
            self.helpers, self.omitted_helpers = [], 0

    def _prune(self):
        while self.frames and (len(self.frames) > WORKING_MEMORY_DECISIONS
                               or size(self.frames) > WORKING_MEMORY_BYTES):
            self.frames.pop(0)
            self.omitted += 1

    def view(self):
        return {**working_memory_policy(), "frames": deepcopy(self.frames),
                "omitted_decisions": self.omitted,
                "meaning": "Historical public records, not current state or verified agent conclusions."}


def restore_working_memory(snapshot, prefix, observation):
    memory = WorkingMemory()
    for event in prefix:
        memory.consume(event)
    memory.observe(observation)
    if digest(memory.view()) != digest(snapshot):
        raise ValueError("WORKING_MEMORY_SNAPSHOT_MISMATCH")
    return memory


def trim_oldest(context):
    memory = context.working_memory
    if not memory or not memory["frames"]:
        return False
    memory["frames"].pop(0)
    memory["omitted_decisions"] += 1
    memory["request_pruned_decisions"] = memory.get("request_pruned_decisions", 0) + 1
    return True


def maintenance(context, loaded_results):
    """Warn while retained information remains available to save."""
    frames = context.working_memory["frames"]
    imminent = frames[:1] if len(frames) >= WORKING_MEMORY_DECISIONS else []
    context.notebook_maintenance = {
        "oldest_decision_leaves_after_action": (
            {k: imminent[0][k] for k in ("episode_id", "decision_id")} if imminent else None
        ),
        "next_helper_may_clear_older_results": loaded_results >= RETAINED_HELPER_RESULTS,
        "message": (
            "Preserve useful conclusions before acting or loading more information clears older context. "
            "Update changed notes; unchanged notes need no write. note_update can accompany your action."
        ),
    }
