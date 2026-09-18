"""Outcome presentation uses recorded public evidence; no native or paid calls."""

from copy import deepcopy

import pytest

from balatro_horizons.agents.outcomes import previous_action_outcome


def evidence(action="play_hand", phase="SELECTING_HAND"):
    delta = {
        "from_observation_id": 17, "to_observation_id": 18,
        "action_type": action, "changes": [
            {"path": ["resources", "chips"], "before": "0", "after": "300"},
            {"path": ["resources", "hands"], "before": 4, "after": 3},
        ],
    }
    observation = {"observation_id": 18, "phase": phase,
                   "state": {"resources": {"money": "0", "chips": "300", "target": "600",
                                           "hands": 3, "discards": 4}},
                   "last_action": delta}
    frame = {"episode_id": "a" * 32, "decision_id": 17, "action_event_id": "commit-17",
             "action": {"type": action}, "observed_result": deepcopy(delta),
             "recorded_decision_note": "This should clear the blind at about 610."}
    return observation, {"frames": [frame]}


def test_model_claim_cannot_turn_300_of_600_into_a_clear():
    observation, memory = evidence()
    outcome = previous_action_outcome(observation, memory)
    assert outcome["recorded_decision_note"] == memory["frames"][0]["recorded_decision_note"]
    assert outcome["scoring"] == {
        "blind_status": "still_in_progress", "chip_total_before": "0",
        "chip_total_after": "300", "target_before": "600", "hands_remaining": 3,
        "discards_remaining": 4, "hand_score": None, "scoring_breakdown": None,
    }
    assert outcome["note_source"] == "agent_claim_before_action"
    assert memory["frames"][0]["recorded_decision_note"].startswith("This should")


def test_cashout_phase_proves_clear_without_inventing_score_after_counter_reset():
    observation, memory = evidence(phase="ROUND_EVAL")
    observation["state"]["resources"].update(chips="0", target="0")
    observation["last_action"]["changes"] = [
        {"path": ["phase"], "before": "SELECTING_HAND", "after": "ROUND_EVAL"},
        {"path": ["resources", "target"], "before": "600", "after": "0"},
    ]
    outcome = previous_action_outcome(observation, memory)
    assert outcome["scoring"]["blind_status"] == "cleared"
    assert outcome["scoring"]["target_before"] == "600"
    assert outcome["scoring"]["hand_score"] is None
    assert outcome["recorded_decision_note"] is None  # Mismatched frame is not paired.


def test_reroll_quote_and_net_change_are_distinct_from_actual_charge():
    observation, memory = evidence(action="reroll_shop", phase="SHOP")
    observation["state"]["resources"]["money"] = "21"
    observation["last_action"]["changes"] = [
        {"path": ["resources", "money"], "before": "23", "after": "21"}]
    receipt = {"quoted_cash_charge": "5", "actual_cash_charge": None,
               "cash_before": "23", "cash_after": "21", "net_cash_change": "-2"}
    observation["last_action"]["transaction"] = receipt
    outcome = previous_action_outcome(observation, memory)
    assert (outcome["cash_before"], outcome["cash_after"]) == ("23", "21")
    assert outcome["transaction"] == receipt
    assert "scoring" not in outcome
    outcome["transaction"]["actual_cash_charge"] = "99"
    assert receipt["actual_cash_charge"] is None


def test_missing_pruned_future_or_unrelated_evidence_does_not_fabricate_a_note():
    observation, memory = evidence()
    assert previous_action_outcome({**observation, "last_action": None}, memory) is None
    assert previous_action_outcome({**observation, "observation_id": 17}, memory) is None
    assert previous_action_outcome(observation, {"frames": []})["recorded_decision_note"] is None
    memory["frames"][0]["decision_id"] = 19
    assert previous_action_outcome(observation, memory)["recorded_decision_note"] is None
    observation["phase"] = "UNKNOWN_PHASE"
    assert previous_action_outcome(observation, memory)["scoring"]["blind_status"] == "unknown"


@pytest.mark.parametrize("before_id", [18, 19, "17", True])
def test_nonhistorical_or_malformed_boundaries_are_not_outcomes(before_id):
    observation, memory = evidence()
    observation["last_action"]["from_observation_id"] = before_id
    assert previous_action_outcome(observation, memory) is None


def test_outcome_does_not_change_evidence_or_import_arbitrary_context():
    observation, memory = evidence()
    memory["frames"][0]["private_reasoning"] = "PRIVATE_SENTINEL"
    memory["frames"][0]["helpers"] = [{"result": "UNRELATED_HELPER_SENTINEL"}]
    before = deepcopy((observation, memory))
    outcome = previous_action_outcome(observation, memory)
    assert (observation, memory) == before
    assert "SENTINEL" not in str(outcome)
    assert outcome["action_reference"] == {
        "episode_id": "a" * 32, "action_event_id": "commit-17", "decision_id": 17,
    }
