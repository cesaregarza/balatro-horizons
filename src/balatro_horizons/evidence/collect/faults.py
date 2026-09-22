"""Collect native acknowledgement-loss and unknown-status evidence."""

from __future__ import annotations

import json
import uuid
from typing import Any

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.provenance import continuation_fingerprint
from balatro_horizons.game.contract import EvaluatorSession, NativeFailure
from balatro_horizons.game.session import NativeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.storage.journal import Store, atomic_json


def _native_game(environment: Any, seed: str) -> EvaluatorSession:
    return NativeGame(environment, seed, calibration=True)


def exercise(unknown: bool, config: Any, *, game_factory=None) -> dict:
    store = Store(ROOT / "data")
    seed = uuid.uuid4().hex[:8].upper()
    factory = game_factory or _native_game
    session: EvaluatorSession = factory(config.environment, seed)
    lost: list[str] = []

    def wrapper(send):
        def request(method, params=None, request_id=None):
            if method == "bh_request_status" and unknown:
                return {"status": "unknown"}
            if method == "select" and not lost:
                send(method, params, request_id)
                session.wait_ready()
                before = continuation_fingerprint(session.observe_private())
                send(method, params, request_id)
                session.wait_ready()
                assert before == continuation_fingerprint(session.observe_private())
                lost.append(request_id)
                raise NativeFailure("INJECTED_ACKNOWLEDGMENT_LOSS")
            return send(method, params, request_id)

        return request

    with session.intercept_rpc_for_calibration(wrapper):
        spending = Spending.episode_only(
            store.root / "private_runs" / f"native-fault-{seed}-spending.json",
            config.budgets.max_batch_cost_usd,
        )
        summary = Runner(store, config, session, Baseline("heuristic"), spending).run(
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
        assert summary["outcome"] == "INFRASTRUCTURE_FAILURE"
        assert summary["committed_actions"] == 0
    else:
        assert summary["outcome"] in ("WIN", "GAME_LOSS")
        assert summary["committed_actions"] >= 1
    return {
        "episode_id": summary["episode_id"],
        "outcome": summary["outcome"],
        "unknown_status": unknown,
        "duplicate_had_no_effect": True,
        "lost_acknowledgments": len(lost),
    }


def collect(config: Any, *, game_factory=None) -> list[dict]:
    """Keep the ambiguous-status test last because it retires its process."""
    results = [
        exercise(False, config, game_factory=game_factory),
        exercise(True, config, game_factory=game_factory),
    ]
    atomic_json(ROOT / "reports/verification/native-faults.json", {"tests": results})
    print(json.dumps(results), flush=True)
    return results
