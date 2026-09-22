import pytest

from balatro_horizons.evidence.collect.acceptance import completed_action_fixture
from balatro_horizons.storage.journal import digest


def test_reuse_requires_completed_journal_and_same_environment(store):
    environment = {"fixture": "pinned-native-environment"}
    eid = store.create({"fixture": "native_action_coverage", "evidence_kind": "NATIVE"})
    store.private_json(
        eid,
        "checkpoint-0.json",
        {
            "game": {"environment": environment},
            "observation": {"phase": "BLIND_SELECT", "observation_id": 0},
        },
    )
    store.append(eid, "action_commit", {"action": {"type": "select_blind"}})
    with pytest.raises(ValueError, match="ACTION_COLLECTION_NOT_COMPLETE"):
        completed_action_fixture(store, eid, digest(environment))
    store.finish(eid, {"outcome": "OPERATOR_ABORT", "reason": "NATIVE_FIXTURE_COMPLETE"})
    result = completed_action_fixture(store, eid, digest(environment))
    assert result["actions"] == ["select_blind"]
    assert result["checkpoints"] == {"BLIND_SELECT": 0}
    with pytest.raises(ValueError, match="ACTION_COLLECTION_ENVIRONMENT_CHANGED"):
        completed_action_fixture(store, eid, "different-environment")
