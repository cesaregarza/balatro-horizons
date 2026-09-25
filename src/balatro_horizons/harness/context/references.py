"""Short model references; canonical public IDs and journals stay unchanged."""

import json
import re
from copy import deepcopy

OBJECT_IDS = frozenset({
    "id", "blind_id", "offer_id", "owned_id", "consumable_id", "current_blind_id",
})
OBJECT_LISTS = frozenset({
    "card_ids", "ordered_ids", "target_ids", "required_ids", "current_hand_ids",
})
AUDIT_FIELDS = frozenset({
    "hash", "previous_hash", "content_hash", "entry_hash", "public_state_hash",
    "event_id", "action_event_id", "request_id",
})
VERBATIM_FIELDS = frozenset({"entries", "entry", "counters", "effects", "note_update"})
HANDLE = re.compile(r"h_[0-9a-f]{20}")


def indexed_tools(definitions):
    """Keep a static schema; indices are resolved locally, never schema enums."""
    result = deepcopy(definitions)
    for tool in result:
        props = tool["parameters"]["properties"]
        for name in OBJECT_IDS & props.keys():
            props[name] = {"type": "integer", "minimum": 1}
        for name in OBJECT_LISTS & props.keys():
            props[name]["items"] = {"type": "integer", "minimum": 1}
        if "episode_id" in props:
            props["episode_id"] = {"type": ["integer", "null"], "minimum": 1}
        for name in ("note_update", "target_ids"):
            if name in props:
                props[name].pop("description", None)
    return result


class ModelReferences:
    """Stable first-public-appearance indices, reconstructed from the public prefix.

    Allocate from observations, never native IDs or hidden state. Sorting dictionary
    keys makes live allocation identical to rehydration from sorted journal JSON.
    Removed objects keep their number; legality still comes from the current board.
    """

    def __init__(self):
        self.objects = {}
        self.episodes = {}
        self._object_ids = []
        self._episode_ids = []

    @staticmethod
    def _index(table, identifiers, identifier):
        if not isinstance(identifier, str):
            return identifier
        if identifier not in table:
            table[identifier] = len(table) + 1
            identifiers.append(identifier)
        return table[identifier]

    def consume(self, event):
        self._index(self.episodes, self._episode_ids, event["episode_id"])
        if event["type"] == "observation":
            self.observe(event["payload"])

    def observe(self, observation):
        self.project(observation)

    def project(self, value, key=None):
        if key in VERBATIM_FIELDS:
            return deepcopy(value)
        if key in OBJECT_IDS:
            return self._index(self.objects, self._object_ids, value)
        if key == "episode_id":
            return self._index(self.episodes, self._episode_ids, value)
        if key in OBJECT_LISTS and isinstance(value, list):
            return [self._index(self.objects, self._object_ids, item) for item in value]
        if key == "path" and isinstance(value, list):
            return [self._index(self.objects, self._object_ids, item) if isinstance(item, str)
                    and HANDLE.fullmatch(item) else item for item in value]
        if key == "summary" and isinstance(value, str):
            return self._summary(value)
        if isinstance(value, dict):
            return self._object(value)
        if isinstance(value, list):
            return [self.project(item) for item in value]
        return value

    def _object(self, value):
        result = {}
        order_change = isinstance(value.get("path"), list) and value["path"][-1:] == ["order"]
        for key in sorted(value):
            if key in AUDIT_FIELDS:
                continue
            if key == "omitted_event_ids":
                result["omitted_event_count"] = len(value[key])
            elif order_change and key in ("before", "after"):
                result[key] = self.project(value[key], "ordered_ids")
            else:
                result[key] = self.project(value[key], key)
        keys = ["omitted_event_count" if key == "omitted_event_ids" else key
                for key in value if key not in AUDIT_FIELDS]
        return {key: result[key] for key in keys}

    def _summary(self, value):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value  # Plain text is not a structured reference field.
        return json.dumps(self.project(parsed), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _resolve(identifiers, index):
        if type(index) is not int or index < 1:
            raise ValueError("INVALID_MODEL_REFERENCE")
        if index > len(identifiers):
            raise ValueError("UNKNOWN_MODEL_REFERENCE")
        return identifiers[index - 1]

    def arguments(self, arguments, *, fields=None):
        if not isinstance(arguments, dict):
            return arguments
        result = deepcopy(arguments)
        for key, value in arguments.items():
            if fields is not None and key not in fields:
                continue  # Leave undeclared fields to ordinary schema validation.
            if key in OBJECT_IDS:
                result[key] = self._resolve(self._object_ids, value)
            elif key in OBJECT_LISTS:
                if not isinstance(value, list):
                    raise ValueError("INVALID_MODEL_REFERENCE")
                result[key] = [self._resolve(self._object_ids, item) for item in value]
            elif key == "episode_id" and value is not None:
                result[key] = self._resolve(self._episode_ids, value)
        return result
