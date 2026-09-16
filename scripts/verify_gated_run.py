#!/usr/bin/env python3
"""Exercise ordinary certified startup with calibration hooks disabled; no paid calls."""

import json

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store, atomic_json


def main():
    panel = json.loads((ROOT / "private/calibration-seeds.json").read_text())
    store = Store(ROOT / "data")
    summary = RunService(store, ReviewService(store)).execute(
        load_config(ROOT / "configs/pilot.yaml"),
        "heuristic",
        panel["pilot"],
        calibration=False,
        extra={
            "evaluation_eligible": False,
            "validation_purpose": "certified_startup_without_calibration_hooks",
        },
    )
    assert summary["outcome"] in ("WIN", "GAME_LOSS"), summary
    assert summary["provider_calls"] == 0 and summary["cost_usd"] == 0
    raw = json.loads((store.episode_path(summary["episode_id"], True) / "raw-0.json").read_text())
    assert raw["raw_engine"]["bh"]["calibration"] is False
    result = {"summary": summary, "calibration_hooks_enabled": False, "evaluation_eligible": False}
    atomic_json(ROOT / "reports/verification/certified-startup-run.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
