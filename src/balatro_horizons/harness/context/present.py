"""Deterministic presentation and bounded retrieval of already-public information."""

import json
from copy import deepcopy

from balatro_horizons.config import (
    AUTOMATIC_PUBLIC_EVENT_COUNT,
    EVENT_SUMMARY_CHARACTERS,
)
from balatro_horizons.config import HELPER_PAGE_BYTES as PAGE_BYTES
from balatro_horizons.harness.tool_interface import INSPECT_SECTIONS, tool

CARD_DEFAULTS = {
    "face_down": False,
    "rank": None,
    "suit": None,
    "sell_price": None,
    "sellable": False,
    "usable": False,
    "min_targets": 0,
    "max_targets": 0,
}
COUNTER_DEFAULTS = {
    "h_mult": "0",
    "h_size": "0",
    "h_x_mult": "0",
    "mult": "0",
    "t_chips": "0",
    "t_mult": "0",
    "x_mult": "1",
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def focused_observation(observation):
    view = deepcopy(observation)
    view["public_contract_version"] = view.pop("schema_version")
    for key in ("episode_id", "public_state_hash"):
        view.pop(key)
    view["state"].pop("public_deck_knowledge")
    omitted = [
        event["event_id"]
        for event in view["recent_public_events"][:-AUTOMATIC_PUBLIC_EVENT_COUNT]
    ]
    view["recent_public_events"] = view["recent_public_events"][-AUTOMATIC_PUBLIC_EVENT_COUNT:]
    view["details_available"] = list(INSPECT_SECTIONS)
    if view["phase"] == "BLIND_SELECT" and view["state"]["revealed_blinds"]:
        view["current_blind_id"] = view["state"]["revealed_blinds"][0]["id"]
    state = view["state"]
    view["presentation"] = {
        "version": "harness",
        "card_defaults": deepcopy(CARD_DEFAULTS),
        "counter_defaults": deepcopy(COUNTER_DEFAULTS),
        "empty_effects_and_counters_omitted": True,
        "deferred_sections": ["public_deck_knowledge"],
    }
    # Preserve every nondefault counter, active effect, concealed-card flag and
    # ordering. This is field compression, never a ranking of strategic choices.
    for area in ("hand", "jokers", "consumables"):
        for card in state[area]:
            for key, default in CARD_DEFAULTS.items():
                if card.get(key) == default:
                    card.pop(key, None)
            if COUNTER_DEFAULTS.keys() <= card["counters"].keys():
                # Only apply defaults when every defaulted counter was observed.
                # Absent/hidden counters must never be represented as neutral values.
                card["counter_defaults_apply"] = True
                card["counters"] = {
                    k: v for k, v in card["counters"].items() if COUNTER_DEFAULTS.get(k) != v
                }
            for key in ("effects", "counters"):
                if not card[key]:
                    card.pop(key)
    if view["phase"] != "SELECTING_HAND":
        state.pop("hand_levels")
        view["presentation"]["deferred_sections"].append("hand_levels")
    for event in view["recent_public_events"]:
        if len(event["summary"]) > EVENT_SUMMARY_CHARACTERS:
            event["summary"] = event["summary"][:EVENT_SUMMARY_CHARACTERS]
            event["truncated"] = True
    return view, omitted


def focused_tools(definitions):
    result = deepcopy(definitions)
    for i, definition in enumerate(result):
        # These shared fields are explained once in the prompt, not in every tool.
        for name in ("memory_update", "decision_note"):
            definition["parameters"]["properties"].get(name, {}).pop("description", None)
        if definition["name"] == "inspect_state":
            result[i] = tool(
                "inspect_state",
                "Read one public state section as paged JSON text. Start offset at 0; follow next_offset. Does not advance the game.",
                {
                    "section": {"type": "string", "enum": list(INSPECT_SECTIONS)},
                    "offset": {"type": "integer", "minimum": 0},
                },
            )
        elif definition["name"] == "read_history":
            definition["description"] = (
                "Browse compact past-public event records. Offset starts at 0; follow next_offset. "
                "Use read_history_detail for a complete event. Does not advance the game."
            )
        elif definition["name"] == "read_rules":
            definition["description"] = (
                "Read a frozen rule or guide page by visible name or key; index lists keys. "
                "Follow next_key for more text. Does not advance the game."
            )
    result.append(
        tool(
            "read_history_detail",
            "Read one past-public event as paged JSON text. Use its history offset and start byte_offset at 0; follow next_offset.",
            {
                "offset": {"type": "integer", "minimum": 0},
                "byte_offset": {"type": "integer", "minimum": 0},
            },
        )
    )
    return result


def text_page(value, offset, **reference):
    """Offsets are UTF-8 bytes; reject cursors inside a character, never lose bytes."""
    data = (value if isinstance(value, str) else encode(value)).encode()
    if offset < 0 or offset > len(data):
        return {"error": "INVALID_PAGE_OFFSET", "game_advanced": False}
    try:
        data[:offset].decode()
    except UnicodeDecodeError:
        return {"error": "INVALID_PAGE_OFFSET", "game_advanced": False}
    content = data[offset : offset + PAGE_BYTES].decode(errors="ignore")
    end = offset + len(content.encode())
    return {
        **reference,
        "content": content,
        "offset": offset,
        "next_offset": end if end < len(data) else None,
        "complete": end == len(data),
        "total_bytes": len(data),
        "game_advanced": False,
    }


def public_history(events, observation):
    # The runner supplies an inherited branch prefix plus this episode's events.
    # Stop at the current observation even if a caller supplies a completed journal.
    prefix = []
    for event in events:
        if event["type"] in ("observation", "action_commit", "automatic_transition"):
            prefix.append(event)
        if (
            event["type"] == "observation"
            and event.get("episode_id") == observation.episode_id
            and event.get("observation_id") == observation.observation_id
        ):
            return prefix
    # No matching observation means the caller has not established a safe cutoff.
    return []


def history_digest(event, offset):
    payload = event["payload"]
    result = {"offset": offset, "event_id": event["event_id"], "type": event["type"]}
    if event["type"] == "observation":
        state = payload["state"]
        result.update(
            observation_id=event["observation_id"],
            phase=payload["phase"],
            progress=state["progress"],
            resources=state["resources"],
        )
    elif event["type"] == "action_commit":
        result.update(observation_id=payload["observation_id"], action=payload["action"])
    else:
        result["detail_available"] = True
    return result


def _history_page(operation, history, references):
    page = []
    for i in range(operation.offset, min(len(history), operation.offset + operation.limit)):
        row = history_digest(history[i], i)
        if references is not None:
            row = references.project(row)
        if len(encode(page + [row]).encode()) > PAGE_BYTES:
            if not page:
                fallback = {
                    "offset": i, "event_id": history[i]["event_id"],
                    "type": history[i]["type"], "detail_available": True,
                }
                page.append(references.project(fallback) if references is not None else fallback)
            break
        page.append(row)
    end = operation.offset + len(page)
    return {"events": page, "next_offset": end if end < len(history) else None,
            "game_advanced": False}


def _rule_page(operation, rules):
    key, separator, cursor = operation.key.partition("#offset=")
    offset = int(cursor) if separator else 0
    key = rules.get("aliases", {}).get(key.casefold(), key)
    entries = rules.get("entries", rules)
    if key.startswith("guide/"):
        return None  # Existing frozen guide reader owns its continuation keys.
    value = sorted(entries) if key == "index" else entries.get(key)
    if value is None:
        return {"error": "UNKNOWN_RULE", "key": key, "game_advanced": False}
    page = text_page(value, offset, key=key, reference="frozen_rules")
    page["next_key"] = (
        key + "#offset=" + str(page["next_offset"])
        if page.get("next_offset") is not None else None
    )
    return page


def focused_helper(operation, events, rules, observation, *, references=None):
    if operation.kind == "inspect_page":
        public = observation.model_dump(mode="json")
        section = operation.section
        value = (public[section]
                 if section in ("recent_public_events", "action_constraints", "last_action")
                 else public["state"][section])
        if references is not None:
            value = references.project(value)
        return text_page(value, operation.offset, section=section,
                         observation_id=observation.observation_id, format="json")
    if operation.kind in ("history", "history_detail"):
        history = public_history(events, observation)
        if operation.kind == "history_detail":
            if operation.offset >= len(history):
                return {"error": "UNKNOWN_PUBLIC_EVENT", "game_advanced": False}
            value = history[operation.offset]
            if references is not None:
                value = references.project(value)
            return text_page(value, operation.byte_offset,
                             history_offset=operation.offset, format="json")
        return _history_page(operation, history, references)
    if operation.kind == "rules":
        return _rule_page(operation, rules)
    return None
