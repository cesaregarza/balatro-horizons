"""Changed context/wire policies cannot silently migrate frozen executions."""

from copy import deepcopy

import pytest

from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.freeze import freeze_protocol, restore_protocol
from balatro_horizons.storage.journal import digest


@pytest.mark.parametrize(("field", "value", "code"), [
    ("context_policy", None, "AGENT_PROTOCOL_CONTEXT_POLICY_CHANGED"),
    ("context_policy", "rolling_results_v0", "AGENT_PROTOCOL_CONTEXT_POLICY_CHANGED"),
    ("provider_wire_policy", "different_wire", "AGENT_PROTOCOL_WIRE_POLICY_CHANGED"),
])
def test_hash_valid_old_or_wrong_policy_is_not_executable(store, config, monkeypatch,
                                                         field, value, code):
    original = freeze_protocol(config, Baseline("heuristic"), {})
    changed = deepcopy(original)
    if value is None:
        changed.pop(field)
    else:
        changed[field] = value
    monkeypatch.setattr("balatro_horizons.harness.context.freeze.read_protocol",
                        lambda *_args: deepcopy(changed))
    checkpoint = {"agent_protocol": {"episode_id": "a" * 32, "hash": digest(changed)}}
    with pytest.raises(ValueError, match=code):
        restore_protocol(store, checkpoint)
    assert original["context_policy"] == "append_only_decision_v1"
    if value is None:
        assert field not in changed
    else:
        assert changed[field] == value


def test_new_frozen_policy_declares_all_results_but_same_completed_decision_memory(config):
    bundle = freeze_protocol(config, Baseline("heuristic"), {})
    assert bundle["interface"] == "tools_v8"
    assert bundle["context_policy"] == "append_only_decision_v1"
    assert bundle["memory_policy"]["retained_results"] == "all_within_decision"
    assert bundle["memory_policy"]["working_memory"]["helpers_per_decision"] == 3
    assert bundle["memory_policy"]["working_memory"]["max_decisions"] == 3
