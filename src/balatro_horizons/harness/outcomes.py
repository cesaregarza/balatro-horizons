"""Compact action feedback from existing public evidence, without judging the move."""

from copy import deepcopy

VERSION = "previous-action-outcome-v2"


def _resources_before(after, changes):
    before = deepcopy(after)
    for change in changes:
        path = change["path"]
        if len(path) == 2 and path[0] == "resources":
            before[path[1]] = change["before"]
    return before


def _phase_feedback(observation, delta, before, after):
    phase_after = observation["phase"]
    phase_before = phase_after
    for change in delta["changes"]:
        if change["path"] == ["phase"]:
            phase_before = change["before"]
    result = {"phase_before": phase_before, "phase_after": phase_after}
    if delta["action_type"] != "play_hand":
        return result
    status = {
        ("SELECTING_HAND", "ROUND_EVAL"): "cleared",
        ("SELECTING_HAND", "SELECTING_HAND"): "still_in_progress",
    }.get((phase_before, phase_after), "unknown")
    result["scoring"] = {
        "blind_status": status, "chip_total_before": before.get("chips"),
        "chip_total_after": after.get("chips"), "target_before": before.get("target"),
        "hands_remaining": after.get("hands"), "discards_remaining": after.get("discards"),
        "hand_score": None, "scoring_breakdown": None,
    }
    return result


def _paired_note(working_memory, before_id, action_type, delta):
    frames = working_memory.get("frames", [])
    frame = frames[-1] if frames else {}
    # Do not pair a neighboring, trimmed, or inherited note with this result.
    paired = (frame.get("decision_id") == before_id
              and frame.get("action", {}).get("type") == action_type
              and frame.get("observed_result") == delta)
    note = {
        "recorded_decision_note": frame.get("recorded_decision_note") if paired else None,
        "recorded_note_update": deepcopy(frame.get("recorded_note_update")) if paired else None,
        "note_source": "agent_claim_before_action" if paired else "not_retained",
    }
    if paired:
        note["action_reference"] = {key: frame[key]
                                    for key in ("episode_id", "action_event_id", "decision_id")}
    return note


def _pass_transaction(result, delta):
    if delta.get("transaction") is not None:
        result["transaction"] = deepcopy(delta["transaction"])


def previous_action_outcome(observation, working_memory):
    delta = observation.get("last_action")
    if not delta or delta.get("to_observation_id") != observation.get("observation_id"):
        return None
    before_id = delta.get("from_observation_id")
    if type(before_id) is not int or before_id >= observation["observation_id"]:
        return None
    action_type = delta["action_type"]
    after = observation["state"]["resources"]
    before = _resources_before(after, delta["changes"])
    result = {
        "version": VERSION, "source": "observed_public_states",
        "from_observation_id": before_id, "to_observation_id": observation["observation_id"],
        "action_type": action_type, "cash_before": before.get("money"),
        "cash_after": after.get("money"),
    }
    result.update(_paired_note(working_memory, before_id, action_type, delta))
    result.update(_phase_feedback(observation, delta, before, after))
    _pass_transaction(result, delta)
    return result
