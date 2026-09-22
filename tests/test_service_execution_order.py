"""Pure lifecycle-order guards for the extracted service execution helper."""

import threading
from types import SimpleNamespace

import pytest

from balatro_horizons.config import Config
from balatro_horizons.evidence.certification import verify_checkpoint
from balatro_horizons.harness import instructions
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.review.decision_ledger import build_summary
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.service_execution import execute_locked, prepare_execution
from balatro_horizons.workbench.branches import prepare_branch


def test_native_preflight_and_constructor_order(monkeypatch, tmp_path):
    calls = []

    class Store:
        root = tmp_path / "store"

        def create(self, manifest, private):
            calls.append("create-record")
            return "episode"

    class Owner:
        def __init__(self):
            self.store = Store()
            self.review = SimpleNamespace(expose=lambda *args, **kwargs: calls.append("expose"))
            self.stop = threading.Event()
            self.active_id = None

        def policy(self, config, agent):
            calls.append("policy")
            return Baseline("heuristic")

        def _decorate_policy(self, policy, operations, human_steps):
            return policy

        def create_game(self, config, seed, *, offline, calibration):
            calls.append("game-constructor")
            return SimpleNamespace(lock={}, close=lambda: calls.append("close"))

    monkeypatch.setattr(instructions, "load_prompt", lambda root: calls.append("prompt") or b"")
    owner = Owner()
    plan = prepare_execution(
        owner,
        Config(),
        "heuristic",
        "fixture",
        offline=False,
        calibration=False,
        eid=None,
        extra=None,
        resume=None,
        prefix=None,
        operations=None,
        spending=Spending(tmp_path / "spending.json", 1),
        human_steps=0,
        root=tmp_path / "root",
        load_session_fn=lambda: calls.append("session-preflight"),
    )
    execute_locked(owner, plan, run_episode_fn=lambda *args, **kwargs: calls.append("run"))

    assert calls.index("session-preflight") < calls.index("game-constructor")
    assert calls.index("game-constructor") < calls.index("run")


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
