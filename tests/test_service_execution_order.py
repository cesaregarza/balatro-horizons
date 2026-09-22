"""Pure lifecycle-order guards for the extracted service execution helper."""

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from balatro_horizons import service as service_module
from balatro_horizons.config import Config
from balatro_horizons.evidence.certification import verify_checkpoint
from balatro_horizons.harness import instructions
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.review.decision_ledger import build_summary
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.service_execution import ExecutionRequest, execute_locked, prepare_execution
from balatro_horizons.storage.journal import atomic_json, digest
from balatro_horizons.workbench.branches import prepare_branch


def test_native_preflight_and_constructor_order(monkeypatch, tmp_path):
    calls = []

    class Store:
        root = tmp_path / "store"

        def create(self, manifest, private):
            calls.append("create-record")
            return "episode"

    owner = RunService(
        Store(), SimpleNamespace(expose=lambda *args, **kwargs: calls.append("expose"))
    )
    game = SimpleNamespace(lock={}, close=lambda: calls.append("close"))
    monkeypatch.setattr(owner, "policy", lambda *args: calls.append("policy") or Baseline("heuristic"))
    monkeypatch.setattr(owner, "create_game", lambda *args, **kwargs: calls.append("game-constructor") or game)
    monkeypatch.setattr(service_module, "ROOT", tmp_path / "root")
    monkeypatch.setattr(service_module, "load_session", lambda: calls.append("session-preflight"))
    monkeypatch.setattr(service_module, "run_episode", lambda *args, **kwargs: calls.append("run"))
    monkeypatch.setattr(instructions, "load_prompt", lambda root: calls.append("prompt") or b"")
    owner.execute(
        Config(), "heuristic", "fixture", offline=False,
        spending=Spending(tmp_path / "spending.json", 1),
    )

    assert calls.index("session-preflight") < calls.index("create-record")
    assert calls.index("create-record") < calls.index("game-constructor")
    assert calls.index("game-constructor") < calls.index("run")


@pytest.mark.parametrize("offline", [False, True])
def test_frozen_rules_path_does_not_follow_worker_lock(store, config, tmp_path, offline):
    request = ExecutionRequest(config, "heuristic", "fixture", offline, None, None, None, None)
    plan = prepare_execution(
        store, request, Baseline("heuristic"), b"", calibration=False,
        extra=None, assisted=False, root=tmp_path,
    )
    rules = {"core": "frozen rules", "environment_hash": digest({})}
    atomic_json(tmp_path / "private/rules.json", rules)
    moved_lock = tmp_path / "different/worker.lock"
    moved_lock.parent.mkdir()
    plan = replace(plan, lock_path=moved_lock)
    owner = SimpleNamespace(
        store=store, stop=threading.Event(), active_id=plan.eid, error=None,
        create_game=lambda *args, **kwargs: SimpleNamespace(lock={}),
    )
    observed = execute_locked(owner, plan, run_episode_fn=lambda *args, **kwargs: kwargs["rules"])
    assert observed == ({"core": "See the shared rules kernel."} if offline else rules)


@pytest.mark.parametrize("failure_point", ["_log_episode_start", "finish"])
def test_started_child_fallback_refuses_incomplete_accounting(
    store, config, episode, monkeypatch, failure_point,
):
    assert verify_checkpoint(store, config, episode, 2)["status"] == "passed"
    child, checkpoint, prefix = prepare_branch(store, config, episode, 2, "agent_continue")
    original = Runner._log_episode_start

    def fail(self, *args, **kwargs):
        raise RuntimeError("forced runner terminal failure")

    def start_then_fail(self):
        original(self)
        fail(self)

    monkeypatch.setattr(Runner, "finish", fail)
    if failure_point == "_log_episode_start":
        monkeypatch.setattr(Runner, "_log_episode_start", start_then_fail)
    service = RunService(store, ReviewService(store))
    with pytest.raises(RuntimeError, match="forced runner terminal failure"):
        service.execute(config, "heuristic", "fixture", offline=True, eid=child,
                        resume=checkpoint, prefix=prefix)
    events = store.events(child)
    assert any(event["type"] == "episode_start" for event in events)
    assert sum(event["type"] == "action_commit" for event in prefix) == 2
    assert sum(event["type"] == "action_commit" for event in events) == (
        0 if failure_point == "_log_episode_start" else 3
    )
    assert store.summary(child)["reason"] == "EXECUTION_TERMINAL_INCOMPLETE"
    before = service.review.exposure(child)
    with pytest.raises(ValueError, match="^EXECUTION_TERMINAL_INCOMPLETE$"):
        build_summary(store, child)
    assert service.review.exposure(child) == before
    assert service.active_id is None
