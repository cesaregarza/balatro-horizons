"""Collect ordinary native calibration runs without provider calls."""

from __future__ import annotations

import json
import uuid

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.game.contract import EvaluatorSession
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store, atomic_json


class SkillReadBaseline(Baseline):
    """Read two deterministic guide entries before using the heuristic policy."""

    def __init__(self):
        super().__init__("heuristic")
        self.reads = 0

    def decide(self, context, exchanges):
        if self.reads < 2:
            self.reads += 1
            key = "guide/balatro-scoring"
            if self.reads == 2:
                key += "/mechanics"
            return {"kind": "rules", "key": key}
        return super().decide(context, exchanges)


class SkillReadService(RunService):
    def policy(self, config, agent):
        assert agent == "heuristic"
        return SkillReadBaseline()


class SessionRunService(RunService):
    def __init__(self, store, review, game_factory):
        super().__init__(store, review)
        self.game_factory = game_factory

    def create_game(self, config, seed, *, offline=False, calibration=False) -> EvaluatorSession:
        if offline:
            return super().create_game(config, seed, offline=offline, calibration=calibration)
        if not calibration:
            raise ValueError("CALIBRATION_SESSION_REQUIRED")
        return self.game_factory(config.environment, seed)


class SessionSkillReadService(SessionRunService):
    def policy(self, config, agent):
        assert agent == "heuristic"
        return SkillReadBaseline()


def _service(store, read_skills: bool, game_factory):
    review = ReviewService(store)
    if game_factory is None:
        return (SkillReadService if read_skills else RunService)(store, review)
    cls = SessionSkillReadService if read_skills else SessionRunService
    return cls(store, review, game_factory)


def _assert_skill_reads(store, episode_id: str, summary: dict) -> None:
    events = store.events(episode_id)
    helpers = [event for event in events if event["type"] == "helper_result"]
    assert len(helpers) == 2
    assert all(event["observation_id"] == 0 for event in helpers)
    assert all(event["payload"]["result"].get("reference") == "balatro_guide" for event in helpers)
    assert all(event["payload"]["result"]["game_advanced"] is False for event in helpers)
    assert summary["provider_calls"] == 0
    assert (store.episode_path(episode_id, True) / "knowledge.json").exists()


def collect(*, read_skills: bool = False, game_factory=None) -> dict:
    store = Store(ROOT / "data")
    service = _service(store, read_skills, game_factory)
    panel_path = ROOT / "private/calibration-seeds.json"
    panel = json.loads(panel_path.read_text()) if panel_path.exists() else {}
    results = {}
    for preset in ("smoke", "pilot"):
        config = load_config(ROOT / f"configs/{preset}.yaml")
        summary = service.execute(
            config,
            "heuristic",
            panel.get(preset) or uuid.uuid4().hex[:8].upper(),
            calibration=True,
            extra={"verification": "skill_reads_then_heuristic"} if read_skills else None,
        )
        assert summary["outcome"] in ("WIN", "GAME_LOSS"), summary["reason"]
        if read_skills:
            _assert_skill_reads(store, summary["episode_id"], summary)
        results[preset] = summary
        print(json.dumps({"preset": preset, **summary}), flush=True)
    atomic_json(ROOT / "reports/verification/native-runs.json", results)
    return results
