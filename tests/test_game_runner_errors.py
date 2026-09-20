"""Native error envelopes stay private while terminal reasons remain specific."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.game.contract import GameSession, NativeFailure, NativeRejected
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.session import NativeGame
from balatro_horizons.game.transport import raise_rpc_error
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
    assert summary["reason"] == "NATIVE_PUBLIC_LEGALITY_MISMATCH"
    assert _private_errors(store, summary["episode_id"]) == [
        {"phase": "action", "code": "X", "name": "NOT_ALLOWED"}
    ]
    rejected = [event for event in store.events(summary["episode_id"])
                if event["type"] == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"] == {"code": "NATIVE_PUBLIC_LEGALITY_MISMATCH"}


def test_upstream_invalid_blind_scores_agent_rejection_with_private_raw_message(store, config):
    class Upstream(FakeGame):
        def apply_public_action(self, action, issuer, request_id=None):
            raise_rpc_error({"data": {"name": "INVALID_STATE", "message": "INVALID_BLIND"}})

    summary = Runner(store, config, Upstream(), Baseline("heuristic")).run()
    assert summary["outcome"] == "INVALID_EVALUATION"
    assert summary["reason"] == "NATIVE_ACTION_REJECTED"
    assert _private_errors(store, summary["episode_id"]) == [
        {"phase": "action", "code": "NATIVE_ACTION_REJECTED", "name": "INVALID_STATE",
         "message": "INVALID_BLIND"}
    ]
    assert "INVALID_BLIND" not in json.dumps(episode_export(store, summary["episode_id"]))


def test_arbitrary_uppercase_native_code_is_private_not_a_public_reason(store, config):
    code = "SEED_IS_ABCDEF_AND_RNG_STATE_7"

    class Secret(FakeGame):
        def wait_ready(self):
            raise NativeFailure(code)

    summary = Runner(store, config, Secret(), Baseline("heuristic")).run()
    assert summary["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert summary["reason"] == "NATIVE_BRIDGE_FAILURE"
    assert _private_errors(store, summary["episode_id"]) == [
        {"phase": "runtime", "code": code, "name": None}
    ]
    assert code not in json.dumps(episode_export(store, summary["episode_id"]))


def test_malformed_rejected_ledger_status_scores_infrastructure(store, config):
    class Malformed(FakeGame):
        def apply_public_action(self, action, issuer, request_id=None):
            native = NativeGame.__new__(NativeGame)
            native._closed = False
            native.raw = {}
            native.bridge = Mock()
            native.bridge.rpc.side_effect = [
                NativeFailure("ACK_LOST"), {"status": "rejected", "response": None}
            ]
            native.wait_ready = Mock()
            native.apply_public_action(SimpleNamespace(type="select_blind"), None, request_id)

    summary = Runner(store, config, Malformed(), Baseline("heuristic")).run()
    assert summary["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert summary["reason"] == "RPC_ENDPOINT_FAILURE"


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
