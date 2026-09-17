import json
import shutil
from copy import deepcopy

import pytest

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.agents.frozen import restore_protocol
from balatro_horizons.config import ROOT
from balatro_horizons.engine.certification import read_checkpoint, verify_checkpoint
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.review.branches import prepare_branch
from balatro_horizons.runner import Runner


@pytest.mark.parametrize(
    "interface", ["operate_v1", "tools_v2", "tools_v3", "tools_v4", "tools_v5", "tools_v6", "tools_v7"]
)
def test_prompt_edit_during_run_and_before_branch_cannot_change_requests(
    store, config, tmp_path, monkeypatch, interface
):
    prompts = tmp_path / "source/configs/prompts"
    shutil.copytree(ROOT / "configs/prompts", prompts)
    monkeypatch.setattr("balatro_horizons.agents.frozen.ROOT", prompts.parents[1])
    monkeypatch.setattr("balatro_horizons.agents.protocol.ROOT", prompts.parents[1])
    filename = "core.txt" if interface == "operate_v1" else interface.replace("_", "-") + ".txt"
    path = prompts / filename
    original = path.read_text()

    class Policy:
        paid = False
        name = "heuristic"

        def __init__(self):
            self.interface = interface
            self.contexts = []

        def decide(self, ctx, exchanges):
            self.contexts.append(deepcopy(ctx))
            path.write_text("CHANGED PROMPT MUST NOT BE DELIVERED\n")
            if not exchanges:
                return {"kind": "arithmetic", "expression": "1+1"}
            obs = ctx["observation"]
            if obs["observation_id"]:
                return {"kind": "abort", "reason": "test complete"}
            return {
                "kind": "action",
                "envelope": {
                    "observation_id": 0,
                    "action": {
                        "type": "select_blind",
                        "blind_id": obs["state"]["revealed_blinds"][0]["id"],
                    },
                },
            }

    policy = Policy()
    result = Runner(store, config, FakeGame(), policy).run()
    assert result["reason"] == "AGENT_ABORT" and result["committed_actions"] == 1
    assert len(policy.contexts) == 4
    assert all(ctx["prompt"] == original.strip() for ctx in policy.contexts)
    eid = result["episode_id"]
    checkpoint = read_checkpoint(store, eid, 0)
    bundle = restore_protocol(store, checkpoint)
    assert bundle["prompt_utf8"] == original
    assert verify_checkpoint(store, config, eid, 0)["status"] == "passed"
    child, checkpoint, prefix = prepare_branch(store, config, eid, 0, "agent_continue")
    continuation = Policy()
    branched = Runner(store, config, FakeGame(), continuation).run(
        eid=child, resume=checkpoint, history_prefix=prefix
    )
    assert branched["reason"] == "AGENT_ABORT"
    assert all(ctx["prompt"] == original.strip() for ctx in continuation.contexts)
    assert result["agent_protocol"]["hash"] == branched["agent_protocol"]["hash"]
    exported = episode_export(store, child)
    assert exported["agent_protocol"] == branched["agent_protocol"]


def test_protocol_tampering_and_changed_allowance_fail_before_branch(store, config, episode):
    checkpoint = read_checkpoint(store, episode, 0)
    verify_checkpoint(store, config, episode, 0)
    changed = config.model_copy(deep=True)
    changed.budgets.max_helper_calls_per_decision += 1
    count = len(store.list_episodes())
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_CONFIGURATION_CHANGED"):
        prepare_branch(store, changed, episode, 0, "agent_continue")
    assert len(store.list_episodes()) == count
    path = store.episode_path(episode, True) / "agent-protocol.json"
    bundle = json.loads(path.read_text())
    bundle["prompt_utf8"] += "tampered"
    path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_SNAPSHOT_MISMATCH"):
        restore_protocol(store, checkpoint)


def test_missing_protocol_is_explicit_legacy_limit(store, config, episode):
    checkpoint = read_checkpoint(store, episode, 0)
    checkpoint.pop("agent_protocol")
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_SNAPSHOT_MISSING"):
        restore_protocol(store, checkpoint)


def test_config_mutation_does_not_change_running_allowance(store, config):
    class MutatingBaseline(Baseline):
        def decide(self, ctx, exchanges):
            config.budgets.max_game_actions = 1
            return super().decide(ctx, exchanges)

    runner = Runner(store, config, FakeGame(), MutatingBaseline("heuristic"))
    result = runner.run()
    assert result["outcome"] == "WIN" and result["committed_actions"] > 1
    assert runner.limits.max_game_actions == 1500
