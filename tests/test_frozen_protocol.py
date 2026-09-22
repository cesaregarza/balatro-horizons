import json
import shutil
from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.evidence.certification import read_checkpoint, verify_checkpoint
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.freeze import freeze_protocol, restore_protocol
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.harness.outcomes import VERSION as ACTION_OUTCOME_VERSION
from balatro_horizons.storage.journal import digest
from balatro_horizons.workbench.branches import prepare_branch


def test_prompt_edit_during_run_and_before_branch_cannot_change_requests(
    store, config, tmp_path, monkeypatch
):
    prompts = tmp_path / "source/configs/prompts"
    shutil.copytree(ROOT / "configs/prompts", prompts)
    monkeypatch.setattr("balatro_horizons.harness.context.freeze.ROOT", prompts.parents[1])
    monkeypatch.setattr("balatro_horizons.harness.context.build.ROOT", prompts.parents[1])
    path = prompts / "harness.txt"
    original = path.read_text()
    spending = Spending.episode_only(
        store.root / "private_runs" / "test-spending.json",
        config.budgets.max_episode_cost_usd or 1,
    )

    class Policy:
        paid = False
        name = "heuristic"

        def __init__(self):
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
    result = Runner(store, config, FakeGame(), policy, spending).run()
    assert result["reason"] == "AGENT_ABORT" and result["committed_actions"] == 1
    assert len(policy.contexts) == 4
    from balatro_horizons.harness.context.render import render_prompt

    rendered = render_prompt(original.encode()).decode()
    # test_prompt_constants independently pins the renderer's output against config.
    assert all(ctx["prompt"] == rendered.strip() for ctx in policy.contexts)
    eid = result["episode_id"]
    checkpoint = read_checkpoint(store, eid, 0)
    bundle = restore_protocol(store, checkpoint)
    assert bundle["prompt_utf8"] == rendered
    assert verify_checkpoint(store, config, eid, 0)["status"] == "passed"
    child, checkpoint, prefix = prepare_branch(store, config, eid, 0, "agent_continue")
    continuation = Policy()
    branched = Runner(store, config, FakeGame(), continuation, spending).run(
        eid=child, resume=checkpoint, history_prefix=prefix
    )
    assert branched["reason"] == "AGENT_ABORT"
    assert all(ctx["prompt"] == rendered.strip() for ctx in continuation.contexts)
    assert result["agent_protocol"]["hash"] == branched["agent_protocol"]["hash"]
    exported = episode_export(store, child)
    assert exported["agent_protocol"] == branched["agent_protocol"]


@pytest.mark.parametrize("older_version", [None, "previous-action-outcome-v1"])
def test_frozen_outcome_gate_survives_resume_and_branch(store, config, monkeypatch, older_version):
    class RecordingPolicy:
        paid = False
        name = "heuristic"

        def __init__(self):
            self.contexts = []
            self.baseline = Baseline("heuristic")

        def decide(self, ctx, exchanges):
            self.contexts.append(deepcopy(ctx))
            if ctx["observation"]["observation_id"] >= 2:
                return {"kind": "abort", "reason": "test complete"}
            return self.baseline.decide(ctx, exchanges)

    def older_bundle(*args, **kwargs):
        bundle = freeze_protocol(*args, **kwargs)
        if older_version is None:
            bundle["memory_policy"].pop("action_outcome")
        else:
            bundle["memory_policy"]["action_outcome"] = older_version
        return bundle

    old_policy = RecordingPolicy()
    spending = Spending.episode_only(
        store.root / "private_runs" / "test-spending.json",
        config.budgets.max_episode_cost_usd or 1,
    )
    with monkeypatch.context() as patch:
        patch.setattr("balatro_horizons.harness.runtime.freeze_protocol", older_bundle)
        old = Runner(store, config, FakeGame(), old_policy, spending).run()
    assert old["committed_actions"] == 2
    assert len(old_policy.contexts) == 3
    assert all("previous_action_outcome" not in ctx for ctx in old_policy.contexts)
    checkpoint = read_checkpoint(store, old["episode_id"], 1)
    assert restore_protocol(store, checkpoint)["memory_policy"].get("action_outcome") == older_version
    assert verify_checkpoint(store, config, old["episode_id"], 1)["status"] == "passed"
    child, resume, prefix = prepare_branch(store, config, old["episode_id"], 1, "agent_continue")
    branch_policy = RecordingPolicy()
    branched = Runner(store, config, FakeGame(), branch_policy, spending).run(
        eid=child, resume=resume, history_prefix=prefix
    )
    assert branched["committed_actions"] == 2
    assert branch_policy.contexts and all(
        "previous_action_outcome" not in ctx for ctx in branch_policy.contexts
    )

    fresh_policy = RecordingPolicy()
    fresh = Runner(store, config, FakeGame(), fresh_policy, spending).run()
    assert fresh["committed_actions"] == 2
    assert fresh_policy.contexts[1]["previous_action_outcome"]["action_type"] == "select_blind"
    fresh_checkpoint = read_checkpoint(store, fresh["episode_id"], 1)
    assert (restore_protocol(store, fresh_checkpoint)["memory_policy"]["action_outcome"]
            == ACTION_OUTCOME_VERSION)


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


def test_missing_protocol_is_an_explicit_restore_limit(store, config, episode):
    checkpoint = read_checkpoint(store, episode, 0)
    checkpoint.pop("agent_protocol")
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_SNAPSHOT_MISSING"):
        restore_protocol(store, checkpoint)


@pytest.mark.parametrize("human", [False, True])
def test_changed_skill_preset_is_refused_before_continuation(store, config, episode, human):
    from balatro_horizons.harness.context.freeze import validate_continuation

    checkpoint = read_checkpoint(store, episode, 0)
    protocol = restore_protocol(store, checkpoint)
    validate_continuation(protocol, config, "heuristic", human=human)
    changed = config.model_copy(deep=True)
    changed.skills = "none" if config.skills != "none" else "balatro-guide-v1"
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_CONFIGURATION_CHANGED"):
        validate_continuation(protocol, changed, "heuristic", human=human)


def test_retired_frozen_interface_is_rejected_explicitly(store, config, episode):
    checkpoint = read_checkpoint(store, episode, 0)
    reference = checkpoint["agent_protocol"]
    path = store.episode_path(reference["episode_id"], True) / "agent-protocol.json"
    bundle = json.loads(path.read_text())
    bundle["interface"] = "retired-interface"
    path.write_text(json.dumps(bundle))
    checkpoint["agent_protocol"] = {**reference, "hash": digest(bundle)}
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_INTERFACE_RETIRED"):
        restore_protocol(store, checkpoint)


def test_config_mutation_does_not_change_running_allowance(store, config):
    class MutatingBaseline(Baseline):
        def decide(self, ctx, exchanges):
            config.budgets.max_game_actions = 1
            return super().decide(ctx, exchanges)

    spending = Spending.episode_only(
        store.root / "private_runs" / "test-spending.json",
        config.budgets.max_episode_cost_usd or 1,
    )
    runner = Runner(store, config, FakeGame(), MutatingBaseline("heuristic"), spending)
    result = runner.run()
    assert result["outcome"] == "WIN" and result["committed_actions"] > 1
    assert runner.limits.max_game_actions == 1500
