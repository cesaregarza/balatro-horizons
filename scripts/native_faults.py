#!/usr/bin/env python3
"""Real native acknowledgment-loss tests; no provider calls or game-rule changes."""

import json
import uuid

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.native import NativeFailure, NativeGame
from balatro_horizons.engine.provenance import continuation_fingerprint
from balatro_horizons.runner import Runner
from balatro_horizons.storage.journal import Store, atomic_json


def exercise(unknown):
    config = load_config(ROOT / "configs/smoke.yaml")
    store = Store(ROOT / "data")
    seed = uuid.uuid4().hex[:8].upper()
    game = NativeGame(config.environment, seed, calibration=True)
    original = game.bridge.rpc
    lost = []

    def rpc(method, params=None, request_id=None):
        if method == "bh_request_status" and unknown:
            return {"status": "unknown"}
        if method == "select" and not lost:
            original(method, params, request_id)
            game.wait_ready()
            before = continuation_fingerprint(game.observe_private())
            # Same request ID and payload must return the cached result, with no mutation.
            original(method, params, request_id)
            game.wait_ready()
            assert before == continuation_fingerprint(game.observe_private())
            lost.append(request_id)
            raise NativeFailure("INJECTED_ACKNOWLEDGMENT_LOSS")
        return original(method, params, request_id)

    game.bridge.rpc = rpc
    summary = Runner(store, config, game, Baseline("heuristic")).run(
        manifest={
            "evidence_kind": "NATIVE",
            "agent": "heuristic",
            "config": config.public(),
            "evaluation_eligible": False,
            "fixture": "unknown_status" if unknown else "lost_ack",
        },
        private={"seed": seed, "config": config.model_dump()},
    )
    assert len(lost) == 1
    if unknown:
        assert summary["outcome"] == "INFRASTRUCTURE_FAILURE" and summary["committed_actions"] == 0
    else:
        assert summary["outcome"] in ("WIN", "GAME_LOSS") and summary["committed_actions"] >= 1
    return {
        "episode_id": summary["episode_id"],
        "outcome": summary["outcome"],
        "unknown_status": unknown,
        "duplicate_had_no_effect": True,
        "lost_acknowledgments": len(lost),
    }


def main():
    results = [exercise(False), exercise(True)]
    atomic_json(ROOT / "reports/verification/native-faults.json", {"tests": results})
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
