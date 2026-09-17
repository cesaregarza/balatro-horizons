#!/usr/bin/env python3
"""Record ordinary-mechanics autonomous calibration runs for Red/White and Red/Gold."""

import argparse
import json
import uuid

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.native import NativeSession
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store, atomic_json


class SkillReadBaseline(Baseline):
    """A deterministic retrieval probe followed by the unchanged heuristic baseline."""

    def __init__(self):
        super().__init__("heuristic")
        self.reads = 0

    def decide(self, ctx, exchanges):
        if self.reads == 0:
            self.reads += 1
            return {"kind": "rules", "key": "guide/balatro-scoring"}
        if self.reads == 1:
            self.reads += 1
            return {"kind": "rules", "key": "guide/balatro-scoring/mechanics"}
        return super().decide(ctx, exchanges)


class SkillReadService(RunService):
    def policy(self, config, agent):
        assert agent == "heuristic"
        return SkillReadBaseline()


class SessionRunService(RunService):
    def __init__(self, store, review, game_factory):
        super().__init__(store, review)
        self.game_factory = game_factory

    def create_game(self, config, seed, *, offline=False, calibration=False):
        if offline:
            return super().create_game(config, seed, offline=offline, calibration=calibration)
        if not calibration:
            raise ValueError("CALIBRATION_SESSION_REQUIRED")
        return self.game_factory(config.environment, seed)


class SessionSkillReadService(SessionRunService):
    def policy(self, config, agent):
        assert agent == "heuristic"
        return SkillReadBaseline()


def collect(*, read_skills=False, game_factory=None):
    store = Store(ROOT / "data")
    review = ReviewService(store)
    if game_factory:
        service_class = SessionSkillReadService if read_skills else SessionRunService
        service = service_class(store, review, game_factory)
    else:
        service_class = SkillReadService if read_skills else RunService
        service = service_class(store, review)
    results = {}
    panel_path = ROOT / "private/calibration-seeds.json"
    panel = json.loads(panel_path.read_text()) if panel_path.exists() else {}
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
            events = store.events(summary["episode_id"])
            helpers = [e for e in events if e["type"] == "helper_result"]
            assert len(helpers) == 2
            assert all(e["observation_id"] == 0 for e in helpers)
            assert all(e["payload"]["result"].get("reference") == "balatro_guide" for e in helpers)
            assert all(e["payload"]["result"]["game_advanced"] is False for e in helpers)
            assert summary["provider_calls"] == 0
            assert (store.episode_path(summary["episode_id"], True) / "knowledge.json").exists()
        results[preset] = summary
        print(json.dumps({"preset": preset, **summary}), flush=True)
    atomic_json(ROOT / "reports/verification/native-runs.json", results)
    return results


def main(*, read_skills=False, game_factory=None, session_factory=NativeSession):
    if game_factory is not None:
        return collect(read_skills=read_skills, game_factory=game_factory)
    # The standalone command is one owned process for both ordinary stakes.
    smoke = load_config(ROOT / "configs/smoke.yaml")
    with session_factory(smoke.environment, reason="startup") as session:
        return collect(read_skills=read_skills, game_factory=session.new_game)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-skills",
        action="store_true",
        help="Read one skill and reference before each unpaid calibration run.",
    )
    main(read_skills=parser.parse_args().read_skills)
