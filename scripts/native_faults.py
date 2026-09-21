#!/usr/bin/env python3
"""Real native acknowledgment-loss tests; no provider calls or game-rule changes."""

import json
import uuid

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.evidence.provenance import continuation_fingerprint
from balatro_horizons.game.session import NativeFailure, NativeGame, NativeSession
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.storage.journal import Store, atomic_json


def exercise(unknown, config, *, game_factory=None):
    store = Store(ROOT / "data")
    seed = uuid.uuid4().hex[:8].upper()
    factory = game_factory or (
        lambda environment, game_seed: NativeGame(environment, game_seed, calibration=True)
    )
    game = factory(config.environment, seed)
    lost = []

    def wrapper(original):
        def rpc(method, params=None, request_id=None):
            if method == "bh_request_status" and unknown:
                return {"status": "unknown"}
            if method == "select" and not lost:
                original(method, params, request_id)
                game.wait_ready()
                before = continuation_fingerprint(game.observe_private())
                # The same request ID must return its cached response, without mutation.
                original(method, params, request_id)
                game.wait_ready()
                assert before == continuation_fingerprint(game.observe_private())
                lost.append(request_id)
                raise NativeFailure("INJECTED_ACKNOWLEDGMENT_LOSS")
            return original(method, params, request_id)
        return rpc

    with game.intercept_rpc_for_calibration(wrapper):
        spending = Spending.episode_only(
            store.root / "private_runs" / f"native-fault-{seed}-spending.json",
            config.budgets.max_episode_cost_usd or 1,
        )
        summary = Runner(store, config, game, Baseline("heuristic"), spending).run(
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


def collect(config, *, game_factory=None):
    # Ambiguous status is deliberately last; it ends its episode and retires the shared process.
    results = [
        exercise(False, config, game_factory=game_factory),
        exercise(True, config, game_factory=game_factory),
    ]
    atomic_json(ROOT / "reports/verification/native-faults.json", {"tests": results})
    print(json.dumps(results), flush=True)
    return results


def main(*, game_factory=None, session_factory=NativeSession):
    config = load_config(ROOT / "configs/smoke.yaml")
    if game_factory is None:
        with session_factory(config.environment, reason="startup") as session:
            return collect(config, game_factory=session.new_game)
    return collect(config, game_factory=game_factory)


if __name__ == "__main__":
    main()
