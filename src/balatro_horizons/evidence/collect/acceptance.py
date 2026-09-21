"""Collect named evaluator fixtures through the public action adapter."""

from __future__ import annotations

import json
import uuid
from typing import Any

from balatro_horizons.actions.validation import validate_action
from balatro_horizons.config import ROOT
from balatro_horizons.contracts import ActionEnvelope, RemainingBudget
from balatro_horizons.evidence.collect.action_table import (
    fixture,
    run_action_table,
    run_shop_table,
    win_rows,
)
from balatro_horizons.evidence.provenance import (
    continuation_fingerprint,
    implementation_fingerprint,
)
from balatro_horizons.game.contract import EvaluatorSession
from balatro_horizons.game.session import NativeGame
from balatro_horizons.observations.projection import HandleIssuer, project_public
from balatro_horizons.storage.journal import Store, atomic_json, digest


def _native_game(environment: Any, seed: str) -> EvaluatorSession:
    return NativeGame(environment, seed, calibration=True)


class Audit:
    """Small evaluator-owned journal around one calibration session."""

    def __init__(self, config: Any, case: str, *, game_factory=None):
        self.config = config
        self.store = Store(ROOT / "data")
        panel_path = ROOT / "private/calibration-seeds.json"
        panel = json.loads(panel_path.read_text()) if panel_path.exists() else {}
        self.seed = panel.get(case) or uuid.uuid4().hex[:8].upper()
        self.eid = self.store.create(
            {
                "evidence_kind": "NATIVE",
                "agent": "evaluator_fixture",
                "config": config.public(),
                "evaluation_eligible": False,
                "fixture": case,
            },
            {"seed": self.seed, "config": config.model_dump()},
        )
        factory = game_factory or _native_game
        self.game_factory = factory
        self.game: EvaluatorSession = factory(config.environment, self.seed)
        self.finished = False
        self.result = None
        self.issuer = HandleIssuer()
        self.decision = 0
        self.actions: list[str] = []
        self.checkpoints: dict[str, int] = {}
        try:
            self.observe()
            self.capture()
        except BaseException:
            self.finished = True
            self.game.close()
            raise
        print(json.dumps({"fixture": case, "episode_id": self.eid}), flush=True)

    def observe(self):
        self.game.wait_ready()
        private_state = self.game.observe_private()
        self.private_state = private_state
        self.obs = project_public(
            private_state,
            episode_id=self.eid,
            observation_id=self.decision,
            issuer=self.issuer,
            memory="",
            remaining_budget=RemainingBudget(game_actions=1500, provider_calls=2000),
        )
        self.store.private_json(self.eid, f"raw-{self.decision}.json", private_state)
        self.store.append(
            self.eid, "observation", self.obs.model_dump(mode="json"), observation_id=self.decision
        )
        return self.obs

    def take(self, action: dict) -> str:
        envelope = ActionEnvelope.model_validate(
            {"observation_id": self.decision, "action": action}
        )
        validate_action(envelope, self.obs)
        request_id = uuid.uuid4().hex
        payload = envelope.model_dump(mode="json")
        self.store.append(
            self.eid,
            "action_intent",
            payload,
            actor="evaluator_fixture",
            observation_id=self.decision,
            request_id=request_id,
        )
        self.game.apply_public_action(envelope.action, self.issuer, request_id)
        self.store.append(
            self.eid,
            "action_commit",
            payload,
            actor="evaluator_fixture",
            observation_id=self.decision,
            request_id=request_id,
        )
        self.actions.append(action["type"])
        self.decision += 1
        self.observe()
        print(
            json.dumps(
                {"action": action["type"], "phase": self.obs.phase, "decision": self.decision}
            ),
            flush=True,
        )
        return request_id

    def fixture(self, case: str) -> None:
        self.store.append(self.eid, "evaluator_fixture", {"case": case}, actor="evaluator_fixture")
        self.game.fixture(case)
        self.decision += 1
        self.observe()

    def capture(self) -> None:
        checkpoint = {
            "implementation_hash": implementation_fingerprint(),
            "continuation_hash": continuation_fingerprint(self.private_state),
            "game": self.game.checkpoint(),
            "issuer": self.issuer.snapshot(),
            "observation": self.obs.model_dump(mode="json"),
            "memory": "",
            "committed": len(self.actions),
            "calls": 0,
            "cost": 0,
            "recent": [],
            "public_prefix_hash": self.store.events(self.eid)[-1]["hash"],
        }
        self.store.private_json(self.eid, f"checkpoint-{self.decision}.json", checkpoint)
        self.checkpoints[self.obs.phase] = self.decision

    def finish(self, reason: str = "NATIVE_FIXTURE_COMPLETE"):
        if self.finished:
            return self.result
        try:
            outcome = self.game.terminal_status() or "OPERATOR_ABORT"
            self.store.finish(
                self.eid,
                {
                    "episode_id": self.eid,
                    "evidence_kind": "NATIVE",
                    "outcome": outcome,
                    "reason": reason,
                    "committed_actions": len(self.actions),
                    "provider_calls": 0,
                    "cost_usd": 0,
                },
            )
            self.result = {
                "episode_id": self.eid,
                "actions": self.actions,
                "checkpoints": self.checkpoints,
                "outcome": outcome,
            }
        finally:
            self.finished = True
            self.game.close()
        return self.result


def exercise_shop(config: Any, *, game_factory=None):
    """Run the complete action/expected-state table for shop coverage."""
    audit = Audit(config, "native_action_coverage", game_factory=game_factory)
    try:
        run_shop_table(audit)
        return audit.finish()
    except Exception:
        audit.finish("NATIVE_FIXTURE_FAILED")
        raise


def exercise_win(config: Any, *, game_factory=None):
    audit = Audit(config, "native_win_detection", game_factory=game_factory)
    try:
        fixture(audit, "win_setup")
        assert audit.game.terminal_status() is None
        run_action_table(audit, win_rows()[:1])
        fixture(audit, "easy_blind")
        run_action_table(audit, win_rows()[1:])
        return audit.finish()
    except Exception:
        audit.finish("NATIVE_FIXTURE_FAILED")
        raise


def _reorder_action(audit: Audit, area: str) -> tuple[dict, list[int], dict, dict]:
    cards = getattr(audit.obs.state, area)
    indices = list(range(len(cards)))
    indices[-2], indices[-1] = indices[-1], indices[-2]
    action = {"type": "reorder", "area": area, "ordered_ids": [cards[i].id for i in indices]}
    return action, indices, audit.obs.state.resources.model_dump(), audit.obs.state.progress.model_dump()


def _reorder_one(audit: Audit, area: str, checks: list[str], *, duplicate: bool = False) -> None:
    phase = audit.obs.phase
    action, indices, resources, progress = _reorder_action(audit, area)
    request_id = audit.take(action)
    assert audit.obs.phase == phase
    assert [card.id for card in getattr(audit.obs.state, area)] == action["ordered_ids"]
    assert audit.obs.state.resources.model_dump() == resources
    assert audit.obs.state.progress.model_dump() == progress
    checks.append(phase + ":" + area)
    if duplicate:
        expected = continuation_fingerprint(audit.private_state)
        audit.game.replay_request("rearrange", {area: indices}, request_id)
        audit.game.wait_ready()
        assert continuation_fingerprint(audit.game.observe_private()) == expected
        checks.append("duplicate_request_unchanged")


def exercise_reorder(config: Any, *, game_factory=None):
    """Check owned-card reorder boundaries and duplicate request idempotency."""
    audit = Audit(config, "native_reorder_boundaries", game_factory=game_factory)
    checks: list[str] = []
    try:
        fixture(audit, "reorder_inventory")
        audit.capture()
        assert audit.obs.phase == "BLIND_SELECT" and len(audit.obs.state.jokers) == 6
        _reorder_one(audit, "jokers", checks, duplicate=True)
        _reorder_one(audit, "consumables", checks)
        select = {
            "type": "select_blind",
            "blind_id": audit.obs.state.revealed_blinds[0].id,
        }
        audit.take(select)
        for area in ("hand", "jokers", "consumables"):
            _reorder_one(audit, area, checks)
        assert 0 <= float(audit.obs.state.resources.money) < 5
        audit.take({"type": "play_hand", "card_ids": [card.id for card in audit.obs.state.hand[:3]]})
        assert audit.obs.phase == "ROUND_EVAL"
        assert not any(row.kind == "interest" for row in audit.obs.state.settlement.rows)
        checks.append("zero_interest_cashout")
        _reorder_one(audit, "jokers", checks)
        _reorder_one(audit, "consumables", checks)
        audit.capture()
        audit.take({"type": "cash_out"})
        _reorder_one(audit, "jokers", checks)
        _reorder_one(audit, "consumables", checks)
        result = {
            **audit.finish(),
            "status": "passed",
            "checks": checks,
            "implementation_hash": implementation_fingerprint(),
            "environment_hash": digest(audit.game.lock),
        }
        report = ROOT / "reports/verification"
        atomic_json(report / f"native-reorder-{audit.eid}.json", result, immutable=True)
        atomic_json(report / "native-reorder.json", result)
        return result
    except Exception:
        if not audit.store.summary(audit.eid):
            audit.finish("NATIVE_REORDER_FAILED")
        raise


def completed_action_fixture(store: Store, episode_id: str, environment_hash: str) -> dict:
    """Recover a completed action fixture from journals, never terminal output."""
    manifest, summary = store.manifest(episode_id), store.summary(episode_id)
    if (
        manifest.get("fixture") != "native_action_coverage"
        or not summary
        or summary.get("reason") != "NATIVE_FIXTURE_COMPLETE"
    ):
        raise ValueError("ACTION_COLLECTION_NOT_COMPLETE")
    checkpoints = {}
    for path in store.episode_path(episode_id, True).glob("checkpoint-*.json"):
        checkpoint = json.loads(path.read_text())
        if digest(checkpoint["game"]["environment"]) != environment_hash:
            raise ValueError("ACTION_COLLECTION_ENVIRONMENT_CHANGED")
        observation = checkpoint["observation"]
        checkpoints[observation["phase"]] = observation["observation_id"]
    actions = [
        event["payload"]["action"]["type"]
        for event in store.events(episode_id)
        if event["type"] == "action_commit"
    ]
    return {
        "episode_id": episode_id,
        "outcome": summary["outcome"],
        "checkpoints": checkpoints,
        "actions": actions,
    }
