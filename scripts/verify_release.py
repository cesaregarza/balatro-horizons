#!/usr/bin/env python3
"""Serial native release evidence; never calls a paid model or modifies personal saves."""

import argparse
import json
import subprocess
import sys

from native_acceptance import exercise_shop, exercise_win
from native_faults import main as fault_tests
from native_invalid import main as invalid_tests
from native_runs import main as ordinary_runs

from balatro_horizons.config import ROOT, Config, load_config
from balatro_horizons.contracts import AnnotationInput
from balatro_horizons.engine.certification import verify_checkpoint
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store, atomic_json, digest


def command(script, *args):
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume-certification",
        action="store_true",
        help="Reuse completed native collection for the same pinned runtime, then repeat certification and branching.",
    )
    args = parser.parse_args()
    store = Store(ROOT / "data")
    source = implementation_fingerprint()
    config = load_config(ROOT / "configs/pilot.yaml")
    if args.resume_certification:
        collected = json.loads(
            (ROOT / "reports/verification/native-fixtures-final.json").read_text()
        )
        fixture, win = collected["actions"], collected["win"]
        current_environment = digest(
            json.loads((ROOT / "private/environment.lock.json").read_text())
        )
        for stake in ("WHITE", "GOLD"):
            audit = json.loads(
                (ROOT / f"reports/verification/runtime-audit-{stake}.json").read_text()
            )
            if audit["environment_hash"] != current_environment:
                raise ValueError("Cannot reuse collection from another native environment")
    else:
        command("audit_runtime.py", "--preset", "smoke")
        command("audit_runtime.py", "--preset", "pilot")
        invalid_tests()
        fixture = exercise_shop(config)
        win = exercise_win(config)
        atomic_json(
            ROOT / "reports/verification/native-fixtures-final.json",
            {"actions": fixture, "win": win},
        )
        fault_tests()
        ordinary_runs()
    command("native_acceptance.py", "--case", "reorder")
    runs = json.loads((ROOT / "reports/verification/native-runs.json").read_text())
    command("certify_prefix.py", fixture["episode_id"])
    gold = runs["pilot"]["episode_id"]
    command("certify_prefix.py", gold)
    direct = verify_checkpoint(store, config, gold, 0, mode="checkpoint")
    print(json.dumps({"direct_checkpoint": direct}), flush=True)
    # A failed direct check leaves the separately verified seed-prefix capability intact.
    review = ReviewService(store)
    review.expose(
        gold,
        "implementation_verification",
        outcome_seen=True,
        model_identity_seen=True,
        max_event_seen=len(store.events(gold)) - 1,
    )
    session = review.open(gold)
    annotation = review.annotate(
        session["review_token"],
        AnnotationInput.model_validate(
            {
                "start_decision": 0,
                "end_decision": 0,
                "judgment": "unclear",
                "confidence": "low",
                "horizons_in_tension": ["near_term", "long_term"],
                "mechanism_summary": "Implementation verification annotation, not an expert assessment: compare selecting the blind with a skip continuation.",
                "alternative_actions": [
                    "Skip the current blind and let the same baseline continue."
                ],
            }
        ),
    )
    obs = session["view"]["observation"]
    before = store.summary(gold)["journal_head"]
    service = RunService(store, review)
    child = service.branch(
        Config.model_validate(store.manifest(gold, True)["config"]),
        gold,
        0,
        "single_action_override",
        [{"type": "skip_blind", "blind_id": obs["state"]["revealed_blinds"][0]["id"]}],
    )
    service.thread.join()
    summary = store.summary(child)
    assert summary and summary["outcome"] in ("WIN", "GAME_LOSS")
    assert store.summary(gold)["journal_head"] == before
    assert not store.manifest(child)["evaluation_eligible"]
    result = {
        "fixture": fixture,
        "win_fixture": win,
        "ordinary_runs": runs,
        "branch": summary,
        "parent_episode_id": gold,
        "parent_unchanged": True,
        "annotation_id": annotation["annotation_id"],
        "direct_checkpoint": direct,
        "environment_hash": digest(
            json.loads((ROOT / "private/environment.lock.json").read_text())
        ),
        "implementation_hash": source,
    }
    result["collection_started_with_implementation_hash"] = source
    result["implementation_hash"] = implementation_fingerprint()
    atomic_json(ROOT / "reports/verification/native-release.json", result)
    print(
        json.dumps({"native_release_evidence": True, "branch": child, "parent": gold}), flush=True
    )


if __name__ == "__main__":
    main()
