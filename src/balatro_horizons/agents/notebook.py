"""Agent-authored notes: journal mutations are authoritative; snapshots are checked caches."""

from copy import deepcopy

from balatro_horizons.config import MAX_MEMORY_CHARACTERS
from balatro_horizons.storage.journal import digest

VERSION = "run-notebook-v1"
MAX_KEY_CHARACTERS = 64


def notebook_tools(definitions):
    from balatro_horizons.agents.tool_interface import ACTION_MODELS, tool

    result = deepcopy(definitions)
    for definition in result:
        if definition["name"] in ACTION_MODELS:
            schema = definition["parameters"]
            schema["properties"].pop("memory_update", None)
            schema["required"].remove("memory_update")
    key = {"type": "string", "minLength": 1, "maxLength": MAX_KEY_CHARACTERS}
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
        isinstance(key, str) and 0 < len(key) <= MAX_KEY_CHARACTERS and bool(key.strip())
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
