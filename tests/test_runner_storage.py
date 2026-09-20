import json

import pytest

from balatro_horizons.agents.baselines import Baseline, ScriptedPolicy
from balatro_horizons.agents.budget import BudgetExhausted, Spending
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.session import NativeFailure
from balatro_horizons.runner import Runner


def test_AT06_every_committed_action_has_explicit_actor(store, episode):
    events = store.events(episode)
    intents = {e["request_id"]: e for e in events if e["type"] == "action_intent"}
    commits = [e for e in events if e["type"] == "action_commit"]
    assert commits
    assert all(
        e["actor"] == "agent" and e["payload"] == intents[e["request_id"]]["payload"]
        for e in commits
    )


def test_AT09_unknown_native_application_is_not_retried(store, config):
    class AppliedThenLost(FakeGame):
        calls = 0

        def apply_public_action(self, *args, **kwargs):
            self.calls += 1
            super().apply_public_action(*args, **kwargs)
            raise NativeFailure("ACTION_STATUS_UNKNOWN")

    game = AppliedThenLost()
    summary = Runner(store, config, game, Baseline("heuristic")).run()
    assert game.calls == 1 and game.committed == 1
    assert summary["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert summary["committed_actions"] == 0


def test_AT12_exactly_one_terminal(store, episode):
    events = store.events(episode)
    assert sum(e["type"] == "terminal" for e in events) == 1
    with pytest.raises(ValueError, match="EPISODE_TERMINATED"):
        store.finish(episode, {"outcome": "GAME_LOSS"})
    assert store.summary(episode)["outcome"] == "WIN"


def test_AT13_torn_journal_recovery_retains_ambiguity(store):
    eid = store.create({"evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "action_intent", {"action": "test"}, request_id="unresolved")
    with (store.episode_path(eid) / "events.jsonl").open("ab") as stream:
        stream.write(b'{"torn":')
    assert store.recover() == [eid]
    assert store.summary(eid)["reason"] == "AMBIGUOUS_ACTION_AFTER_CRASH"
    assert len(list(store.episode_path(eid, True).glob("torn-*.bin"))) == 1
    rows = store.list_episodes()
    store.recover()
    assert store.list_episodes() == rows


def test_AT13_corruption_is_not_silently_repaired(store, episode):
    path = store.episode_path(episode) / "events.jsonl"
    path.write_text(path.read_text().replace("episode_start", "episode_tampered", 1))
    with pytest.raises(ValueError, match="JOURNAL_INTEGRITY_FAILURE"):
        store.events(episode)


def test_AT14_durable_spending_counts_unknown_usage(tmp_path):
    ledger = Spending(tmp_path / "spend.json", cap=1.0)
    ledger.reserve("a", "e", 0.7, 1.0)
    with pytest.raises(BudgetExhausted):
        Spending(tmp_path / "spend.json", 1.0).reserve("b", "e", 0.4, 1.0)
    ledger.settle("a", 0.2)
    ledger.reserve("b", "e", 0.4, 1.0)
    assert sum(
        v["cost"] for v in json.loads((tmp_path / "spend.json").read_text()).values()
    ) == pytest.approx(0.6)


def test_AT14_helpers_do_not_reset_invalid_counter(store, config):
    invalid = {
        "kind": "action",
        "envelope": {"observation_id": 999, "action": {"type": "select_blind", "blind_id": "no"}},
    }
    helper = {"kind": "arithmetic", "expression": "2+2"}
    summary = Runner(
        store, config, FakeGame(), ScriptedPolicy([invalid, helper, invalid, helper, invalid])
    ).run()
    assert summary["outcome"] == "AGENT_PROTOCOL_FAILURE" and summary["committed_actions"] == 0


def test_AT22_offline_evidence_is_explicit(store, episode):
    assert store.manifest(episode)["evidence_kind"] == "SYNTHETIC_TEST"
    assert store.summary(episode)["evidence_kind"] == "SYNTHETIC_TEST"
    export = episode_export(store, episode)
    assert export["manifest"]["evidence_kind"] == "SYNTHETIC_TEST"
    assert "DO_NOT_EXPORT_THIS_SEED" not in json.dumps(export)
