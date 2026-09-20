"""Native error envelopes stay private while terminal reasons remain specific."""

import json

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.game.contract import GameSession, NativeFailure, NativeRejected
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.runner import Runner


def _private_errors(store, eid):
    return [
        json.loads(path.read_text())
        for path in store.episode_path(eid, True).glob("engine-error-*.json")
    ]


def test_scripted_fake_implements_the_runner_game_contract():
    assert isinstance(FakeGame(), GameSession)


def test_rejected_lua_code_is_terminal_reason_and_envelope_is_private(store, config):
    class Rejected(FakeGame):
        def apply_public_action(self, action, issuer, request_id=None):
            raise NativeRejected("X", name="NOT_ALLOWED")

    summary = Runner(store, config, Rejected(), Baseline("heuristic")).run()
    assert summary["outcome"] == "INVALID_EVALUATION"
    assert summary["reason"] == "X"
    assert _private_errors(store, summary["episode_id"]) == [
        {"phase": "action", "code": "X", "name": "NOT_ALLOWED"}
    ]
    rejected = [event for event in store.events(summary["episode_id"])
                if event["type"] == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"] == {"code": "NATIVE_PUBLIC_LEGALITY_MISMATCH"}


def test_infrastructure_lua_code_is_not_collapsed(store, config):
    class Busy(FakeGame):
        def wait_ready(self):
            raise NativeFailure("BUSY", name="INFRASTRUCTURE")

    summary = Runner(store, config, Busy(), Baseline("heuristic")).run()
    assert summary["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert summary["reason"] == "BUSY"
    assert _private_errors(store, summary["episode_id"]) == [
        {"phase": "runtime", "code": "BUSY", "name": "INFRASTRUCTURE"}
    ]
