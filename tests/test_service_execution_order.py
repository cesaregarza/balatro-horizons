"""Pure lifecycle-order guards for the extracted service execution helper."""

import threading
from types import SimpleNamespace

from balatro_horizons.config import Config
from balatro_horizons.harness import instructions
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.money import Spending
from balatro_horizons.service_execution import execute_locked, prepare_execution


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
