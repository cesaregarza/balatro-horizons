#!/usr/bin/env python3
"""Collect explicitly labeled native fixture evidence through the public action adapter."""

import argparse
import copy
import json
import uuid

from balatro_horizons.actions.validation import validate_action
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.contracts import ActionEnvelope, RemainingBudget
from balatro_horizons.engine.native import NativeGame
from balatro_horizons.engine.provenance import continuation_fingerprint, implementation_fingerprint
from balatro_horizons.observations.projection import HandleIssuer, project_public
from balatro_horizons.storage.journal import Store, atomic_json


class Audit:
    def __init__(self, config, case):
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
        self.game = NativeGame(config.environment, self.seed, calibration=True)
        self.issuer = HandleIssuer()
        self.decision = 0
        self.actions = []
        self.checkpoints = {}
        self.observe()
        self.capture()
        print(json.dumps({"fixture": case, "episode_id": self.eid}), flush=True)

    def observe(self):
        self.game.wait_ready()
        self.raw = self.game.observe_private()
        self.obs = project_public(
            self.raw,
            episode_id=self.eid,
            observation_id=self.decision,
            issuer=self.issuer,
            memory="",
            remaining_budget=RemainingBudget(game_actions=1500, provider_calls=2000),
        )
        self.store.private_json(self.eid, f"raw-{self.decision}.json", self.raw)
        self.store.append(
            self.eid, "observation", self.obs.model_dump(mode="json"), observation_id=self.decision
        )
        return self.obs

    def take(self, action):
        env = ActionEnvelope.model_validate({"observation_id": self.decision, "action": action})
        validate_action(env, self.obs)
        rid = uuid.uuid4().hex
        self.store.append(
            self.eid,
            "action_intent",
            env.model_dump(mode="json"),
            actor="evaluator_fixture",
            observation_id=self.decision,
            request_id=rid,
        )
        self.game.apply_public_action(env.action, self.issuer, rid)
        self.store.append(
            self.eid,
            "action_commit",
            env.model_dump(mode="json"),
            actor="evaluator_fixture",
            observation_id=self.decision,
            request_id=rid,
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
        return rid

    def fixture(self, case):
        self.store.append(self.eid, "evaluator_fixture", {"case": case}, actor="evaluator_fixture")
        self.game.bridge.rpc("bh_fixture", {"case": case})
        self.decision += 1
        self.observe()

    def capture(self):
        checkpoint = {
            "implementation_hash": implementation_fingerprint(),
            "continuation_hash": continuation_fingerprint(self.raw),
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

    def finish(self, reason="NATIVE_FIXTURE_COMPLETE"):
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
        self.game.close()
        return {
            "episode_id": self.eid,
            "actions": self.actions,
            "checkpoints": self.checkpoints,
            "outcome": outcome,
        }


def exercise_shop(config):
    a = Audit(config, "native_action_coverage")
    try:
        a.take({"type": "skip_blind", "blind_id": a.obs.state.revealed_blinds[0].id})
        if "skip_pack" in a.obs.available_action_types:
            a.take({"type": "skip_pack"})
        assert a.obs.state.progress.blind == "Big"
        a.take({"type": "select_blind", "blind_id": a.obs.state.revealed_blinds[0].id})
        ids = [c.id for c in a.obs.state.hand]
        a.take({"type": "reorder", "area": "hand", "ordered_ids": ids[::-1]})
        assert [c.id for c in a.obs.state.hand] == ids[::-1]
        discards = a.obs.state.resources.discards
        a.take({"type": "discard", "card_ids": [a.obs.state.hand[0].id]})
        assert a.obs.state.resources.discards == discards - 1
        a.fixture("easy_blind")
        assert a.obs.state.resources.target == "1"
        a.take({"type": "play_hand", "card_ids": [a.obs.state.hand[0].id]})
        assert a.obs.phase == "ROUND_EVAL" and not a.game.terminal_status()
        a.capture()
        a.take({"type": "cash_out"})
        assert a.obs.phase == "SHOP"
        a.fixture("shop")
        assert len(a.obs.state.jokers) == 5 and len(a.obs.state.consumables) == 2
        for area in ("jokers", "consumables"):
            ids = [c.id for c in getattr(a.obs.state, area)]
            a.take({"type": "reorder", "area": area, "ordered_ids": ids[::-1]})
            assert [c.id for c in getattr(a.obs.state, area)] == ids[::-1]
        negative = next(c for c in a.obs.state.offers if c.kind == "joker")
        assert negative.acquire_allowed
        a.take({"type": "buy", "offer_id": negative.id})
        assert len(a.obs.state.jokers) == 6
        hermit = next(c for c in a.obs.state.offers if c.kind == "consumable")
        assert hermit.buy_and_use_allowed
        a.take({"type": "buy", "offer_id": hermit.id, "mode": "buy_and_use", "target_ids": []})
        assert len(a.obs.state.consumables) == 2
        for _ in range(2):
            a.take({"type": "sell", "owned_id": a.obs.state.jokers[0].id})
        voucher = next(c for c in a.obs.state.offers if c.kind == "voucher")
        a.take({"type": "buy", "offer_id": voucher.id})
        pack = next(c for c in a.obs.state.offers if c.kind == "pack")
        a.take({"type": "buy", "offer_id": pack.id})
        assert a.obs.state.resources.pack_choices_remaining == 2
        a.capture()
        a.take({"type": "choose_pack", "offer_id": a.obs.state.offers[0].id, "target_ids": []})
        assert a.obs.state.resources.pack_choices_remaining == 1
        a.take({"type": "choose_pack", "offer_id": a.obs.state.offers[0].id, "target_ids": []})
        assert a.obs.phase == "SHOP"
        a.fixture("another_pack")
        pack = next(c for c in a.obs.state.offers if c.kind == "pack")
        a.take({"type": "buy", "offer_id": pack.id})
        a.take({"type": "skip_pack"})
        assert a.obs.phase == "SHOP"
        a.fixture("credit")
        assert a.obs.state.resources.money == "-5" and a.obs.state.resources.credit_limit == "20"
        a.take({"type": "reroll_shop"})
        assert float(a.obs.state.resources.money) < 0
        a.capture()
        a.take({"type": "leave_shop"})
        a.take({"type": "reroll_boss"})
        a.take({"type": "select_blind", "blind_id": a.obs.state.revealed_blinds[0].id})
        c = a.obs.state.consumables[0]
        assert c.usable and c.min_targets >= 1
        ids = [x.id for x in a.obs.state.hand[: c.min_targets]]
        before = {x.id: x.rank for x in a.obs.state.hand}
        a.take({"type": "use_consumable", "consumable_id": c.id, "target_ids": ids})
        assert len(a.obs.state.consumables) == 1
        assert any(before[x.id] != x.rank for x in a.obs.state.hand if x.id in ids)
        a.capture()
        a.fixture("mask_jokers")
        assert all(
            c.face_down and c.sellable is None and not c.effects and not c.counters
            for c in a.obs.state.jokers
        )
        snapshot = a.issuer.snapshot()
        raw = copy.deepcopy(a.raw)
        raw["visible"]["jokers"].reverse()
        left = project_public(
            a.raw,
            episode_id=a.eid,
            observation_id=a.decision,
            issuer=HandleIssuer.restore(snapshot),
            memory="",
            remaining_budget=RemainingBudget(game_actions=0, provider_calls=0),
        )
        right = project_public(
            raw,
            episode_id=a.eid,
            observation_id=a.decision,
            issuer=HandleIssuer.restore(snapshot),
            memory="",
            remaining_budget=RemainingBudget(game_actions=0, provider_calls=0),
        )
        assert left == right
        return a.finish()
    except Exception:
        a.finish("NATIVE_FIXTURE_FAILED")
        raise


def exercise_win(config):
    a = Audit(config, "native_win_detection")
    try:
        a.fixture("win_setup")
        assert a.game.terminal_status() is None
        a.take({"type": "select_blind", "blind_id": a.obs.state.revealed_blinds[0].id})
        a.fixture("easy_blind")
        a.take({"type": "play_hand", "card_ids": [a.obs.state.hand[0].id]})
        assert a.game.terminal_status() == "WIN"
        return a.finish()
    except Exception:
        a.finish("NATIVE_FIXTURE_FAILED")
        raise


def exercise_reorder(config):
    """Regression for the six-Joker blind-selection failure, including exactly-once replay."""
    from balatro_horizons.storage.journal import digest

    a = Audit(config, "native_reorder_boundaries")
    checks = []
    try:
        a.fixture("reorder_inventory")
        a.capture()
        assert a.obs.phase == "BLIND_SELECT" and len(a.obs.state.jokers) == 6

        def reorder(area, *, duplicate=False):
            phase = a.obs.phase
            before = getattr(a.obs.state, area)
            indices = list(range(len(before)))
            indices[-2], indices[-1] = indices[-1], indices[-2]
            ordered = [before[i].id for i in indices]
            resources = a.obs.state.resources.model_dump()
            progress = a.obs.state.progress.model_dump()
            rid = a.take({"type": "reorder", "area": area, "ordered_ids": ordered})
            assert a.obs.phase == phase
            assert [card.id for card in getattr(a.obs.state, area)] == ordered
            assert a.obs.state.resources.model_dump() == resources
            assert a.obs.state.progress.model_dump() == progress
            checks.append(phase + ":" + area)
            if duplicate:
                expected = continuation_fingerprint(a.raw)
                a.game.bridge.rpc("rearrange", {area: indices}, rid)
                a.game.wait_ready()
                assert continuation_fingerprint(a.game.observe_private()) == expected
                checks.append("duplicate_request_unchanged")

        reorder("jokers", duplicate=True)
        reorder("consumables")
        a.take({"type": "select_blind", "blind_id": a.obs.state.revealed_blinds[0].id})
        for area in ("hand", "jokers", "consumables"):
            reorder(area)
        a.fixture("easy_blind")
        a.take({"type": "play_hand", "card_ids": [a.obs.state.hand[0].id]})
        assert a.obs.phase == "ROUND_EVAL"
        reorder("jokers")
        reorder("consumables")
        a.capture()
        a.take({"type": "cash_out"})
        reorder("jokers")
        reorder("consumables")
        result = {
            **a.finish(),
            "status": "passed",
            "checks": checks,
            "implementation_hash": implementation_fingerprint(),
            "environment_hash": digest(a.game.lock),
        }
        path = ROOT / "reports/verification" / ("native-reorder-" + a.eid + ".json")
        atomic_json(path, result, immutable=True)
        atomic_json(ROOT / "reports/verification/native-reorder.json", result)
        return result
    except Exception:
        if not a.store.summary(a.eid):
            a.finish("NATIVE_REORDER_FAILED")
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", choices=["actions", "win", "reorder", "all"], default="all")
    args = p.parse_args()
    cfg = load_config(ROOT / "configs/smoke.yaml")
    results = {}
    if args.case in ("actions", "all"):
        results["actions"] = exercise_shop(cfg)
    if args.case in ("win", "all"):
        results["win"] = exercise_win(cfg)
    if args.case in ("reorder", "all"):
        results["reorder"] = exercise_reorder(cfg)
    path = ROOT / "reports/verification" / ("native-fixtures-" + uuid.uuid4().hex + ".json")
    atomic_json(path, results, immutable=True)
    print(json.dumps({"result_artifact": str(path), "results": results}), flush=True)


if __name__ == "__main__":
    main()
