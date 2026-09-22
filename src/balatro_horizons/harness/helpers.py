"""Read-only public helper dispatch for a single agent operation."""

from balatro_horizons.harness.arithmetic import arithmetic
from balatro_horizons.harness.context.committed_evidence import retrieve_action_result
from balatro_horizons.harness.context.present import focused_helper
from balatro_horizons.harness.skills import read_guide


def helper(operation, events, rules, observation=None):
    if operation.kind == "action_result":
        return retrieve_action_result(operation, events, observation)
    if observation is not None:
        result = focused_helper(operation, events, rules, observation)
        if result is not None:
            return result
    elif operation.kind in ("inspect_page", "history", "history_detail"):
        raise ValueError("CURRENT_OBSERVATION_REQUIRED")
    if operation.kind == "arithmetic":
        return {"result": arithmetic(operation.expression)}
    if operation.kind == "skill":
        item = next((s for s in rules.get("skills", []) if s["name"] == operation.name), None)
        if item is None:
            return {"error": "UNKNOWN_SKILL", "game_advanced": False}
        return read_guide(rules, item["key"])
    if operation.kind == "rules":
        entries = rules.get("entries", rules)
        key = rules.get("aliases", {}).get(operation.key.casefold(), operation.key)
        if key.startswith("guide/") and (key.partition("#offset=")[0] in entries):
            return read_guide(rules, key)
        if key == "index":
            return {"reference": "frozen_rules", "keys": sorted(entries)}
        return {"key": operation.key, "entry": entries.get(key), "reference": "frozen_rules"}
    if operation.kind == "history":
        public = [e for e in events if e["type"] in
                  ("observation", "action_commit", "automatic_transition")]
        page = public[operation.offset:operation.offset + operation.limit]
        return {
            "events": page,
            "next_offset": operation.offset + len(page)
            if operation.offset + len(page) < len(public) else None,
        }
    raise ValueError("NOT_A_HELPER")
