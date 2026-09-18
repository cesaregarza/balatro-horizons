"""Compact action feedback from existing public evidence, without judging the move."""

from copy import deepcopy

VERSION = "previous-action-outcome-v1"


def previous_action_outcome(observation, working_memory):
    delta = observation.get("last_action")
    if not delta or delta.get("to_observation_id") != observation.get("observation_id"):
        return None
    before_id = delta.get("from_observation_id")
    if type(before_id) is not int or before_id >= observation["observation_id"]:
        return None
    action_type = delta["action_type"]
    after = observation["state"]["resources"]
    before = deepcopy(after)
    phase_after = observation["phase"]
    phase_before = phase_after
    for change in delta["changes"]:
        path = change["path"]
        if path == ["phase"]:
            phase_before = change["before"]
        elif len(path) == 2 and path[0] == "resources":
            before[path[1]] = change["before"]

    frames = working_memory.get("frames", [])
    frame = frames[-1] if frames else {}
    # Do not pair a neighboring/trimmed/inherited note with the wrong result.
    paired = (frame.get("decision_id") == before_id
              and frame.get("action", {}).get("type") == action_type
              and frame.get("observed_result") == delta)
    result = {
        "version": VERSION,
        "source": "observed_public_states",
        "from_observation_id": before_id,
        "to_observation_id": observation["observation_id"],
        "action_type": action_type,
        "recorded_decision_note": frame.get("recorded_decision_note") if paired else None,
        "note_source": "agent_claim_before_action" if paired else "not_retained",
        "phase_before": phase_before,
        "phase_after": phase_after,
        "cash_before": before.get("money"),
        "cash_after": after.get("money"),
    }
    if paired:
        result["action_reference"] = {key: frame[key]
                                      for key in ("episode_id", "action_event_id", "decision_id")}
    if action_type == "play_hand":
        # ROUND_EVAL is the observed completed-blind screen; no score forecast or
        # inference from the model's note is used. Other transitions remain unknown.
        blind_status = "unknown"
        if phase_before == "SELECTING_HAND":
            if phase_after == "ROUND_EVAL":
                blind_status = "cleared"
            elif phase_after == "SELECTING_HAND":
                blind_status = "still_in_progress"
        result["scoring"] = {
            "blind_status": blind_status,
            "chip_total_before": before.get("chips"),
            "chip_total_after": after.get("chips"),
            "target_before": before.get("target"),
            "hands_remaining": after.get("hands"),
            "discards_remaining": after.get("discards"),
            "hand_score": None,
            "scoring_breakdown": None,
        }
    if delta.get("transaction") is not None:
        result["transaction"] = deepcopy(delta["transaction"])
    return result
