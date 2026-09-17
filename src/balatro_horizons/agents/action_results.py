"""Join a committed action to its public evidence within the supplied branch cutoff."""

from balatro_horizons.agents.focused import public_history, text_page
from balatro_horizons.contracts import ActionEnvelope, Observation


def reference(event):
    return {key: event[key] for key in ("episode_id", "event_id", "observation_id")}


def retrieve_action_result(operation, events, observation):
    history = public_history(events, observation)
    commits = [(i, event) for i, event in enumerate(history) if event["type"] == "action_commit"]
    if operation.decision_id is not None:
        commits = [(i, event) for i, event in commits
                   if event["observation_id"] == operation.decision_id]
    if operation.episode_id is not None:
        commits = [(i, event) for i, event in commits if event["episode_id"] == operation.episode_id]
    if not commits:
        return {"error": "ACTION_RESULT_NOT_AVAILABLE", "game_advanced": False}
    if operation.decision_id is not None and len(commits) != 1:
        return {"error": "AMBIGUOUS_ACTION_REFERENCE", "game_advanced": False}
    i, commit = commits[-1]
    before = next((event for event in reversed(history[:i])
                   if event["type"] == "observation"
                   and event["episode_id"] == commit["episode_id"]
                   and event["observation_id"] == commit["observation_id"]), None)
    after = next((event for event in history[i + 1:] if event["type"] == "observation"), None)
    if before is None:
        return {"error": "ACTION_EVIDENCE_INCOMPLETE", "game_advanced": False}
    envelope = ActionEnvelope.model_validate(commit["payload"])
    prior = Observation.model_validate(before["payload"])
    following = Observation.model_validate(after["payload"]) if after else None
    delta = following.last_action if following else None
    if delta and (delta.from_observation_id != prior.observation_id
                  or delta.to_observation_id != following.observation_id
                  or delta.action_type != envelope.action.type):
        return {"error": "ACTION_EVIDENCE_INCOMPLETE", "game_advanced": False}
    refs = {"action": reference(commit), "before": reference(before),
            "after": reference(after) if after else None}
    if operation.section == "before":
        value = prior.model_dump(mode="json")
    elif operation.section == "after":
        value = following.model_dump(mode="json") if following else None
    else:
        value = {
            "action": envelope.action.model_dump(mode="json"),
            "recorded_decision_note": envelope.decision_note,
            "observed_result": delta.model_dump(mode="json") if delta else None,
            "evidence": "public_observation_changes",
            "status": "observed" if following else "result_not_observed",
            "hand_score": None,
            "scoring_breakdown": None,
        }
    return text_page(value, operation.byte_offset, references=refs, section=operation.section,
                     format="json", reference="action_result")
