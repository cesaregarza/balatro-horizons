#!/usr/bin/env python3
"""Serial native release evidence; never calls a paid model or modifies personal saves."""

import argparse
import json
import subprocess
import sys
import uuid

from audit_runtime import audit_session, write_profile_audits
from native_acceptance import exercise_reorder, exercise_shop, exercise_win
from native_faults import collect as fault_tests
from native_invalid import collect as invalid_tests
from native_runs import collect as ordinary_runs

from balatro_horizons.config import ROOT, Config, load_config
from balatro_horizons.contracts import AnnotationInput
from balatro_horizons.evidence.certification import verify_checkpoint
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.game.session import NativeSession
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import Store, atomic_json, digest


def command(script, *args):
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], cwd=ROOT, check=True)


def completed_action_fixture(store, eid, environment_hash):
    """Recover a completed collection from authoritative journals, never terminal output."""
    manifest, summary = store.manifest(eid), store.summary(eid)
    if (
        manifest.get("fixture") != "native_action_coverage"
        or not summary
        or summary.get("reason") != "NATIVE_FIXTURE_COMPLETE"
    ):
        raise ValueError("ACTION_COLLECTION_NOT_COMPLETE")
    checkpoints = {}
    for path in store.episode_path(eid, True).glob("checkpoint-*.json"):
        checkpoint = json.loads(path.read_text())
        if digest(checkpoint["game"]["environment"]) != environment_hash:
            raise ValueError("ACTION_COLLECTION_ENVIRONMENT_CHANGED")
        checkpoints[checkpoint["observation"]["phase"]] = checkpoint["observation"][
            "observation_id"
        ]
    return {
        "episode_id": eid,
        "outcome": summary["outcome"],
        "checkpoints": checkpoints,
        "actions": [
            e["payload"]["action"]["type"]
            for e in store.events(eid)
            if e["type"] == "action_commit"
        ],
    }


def release_plan(*, resume_certification=False, resume_actions=False, gameplay_only=False):
    """Describe process launches and in-process game resets without reading inputs."""
    if gameplay_only:
        stages = [
            {
                "name": "gameplay collection only",
                "physical_launches": 1,
                "launch_reason": "startup; one process reused across all eight gameplay cases",
                "game_resets": 8,
                "cases": [
                    "invalid action",
                    "shop action coverage",
                    "terminal fixture",
                    "ordinary WHITE run",
                    "ordinary GOLD run",
                    "reorder boundaries",
                    "lost acknowledgment",
                    "unknown action status (last)",
                ],
                "restoration_certification": False,
                "capability_activation": False,
            }
        ]
    elif resume_certification:
        stages = [
            {
                "name": "reorder acceptance fixture",
                "physical_launches": 1,
                "launch_reason": "startup for a standalone functional collection",
                "game_resets": 1,
            },
            {
                "name": "fresh-process restoration certification",
                "physical_launches": 9,
                "launch_reason": (
                    "three seed-prefix repetitions for each of two episodes, "
                    "plus three direct-checkpoint repetitions"
                ),
                "game_resets": 9,
            },
            {
                "name": "branch restoration",
                "physical_launches": 1,
                "launch_reason": "explicit child branch launch",
                "game_resets": 1,
            },
        ]
    elif resume_actions:
        stages = [
            {
                "name": "resumed functional collection",
                "physical_launches": 1,
                "launch_reason": "startup; one session reused across remaining cases",
                "game_resets": 6,
            },
            {
                "name": "fresh-process restoration certification",
                "physical_launches": 9,
                "launch_reason": (
                    "three seed-prefix repetitions for each of two episodes, "
                    "plus three direct-checkpoint repetitions"
                ),
                "game_resets": 9,
            },
            {
                "name": "branch restoration",
                "physical_launches": 1,
                "launch_reason": "explicit child branch launch",
                "game_resets": 1,
            },
        ]
    else:
        stages = [
            {
                "name": "startup profile stability",
                "physical_launches": 2,
                "launch_reason": "two fresh-process profile repetitions",
                "game_resets": 4,
                "stakes_per_process": ["WHITE", "GOLD"],
            },
            {
                "name": "functional collection",
                "physical_launches": 0,
                "launch_reason": "reuse of the second profile process",
                "game_resets": 8,
                "cases": [
                    "invalid action",
                    "shop action coverage",
                    "terminal fixture",
                    "ordinary WHITE run",
                    "ordinary GOLD run",
                    "reorder boundaries",
                    "lost acknowledgment",
                    "unknown action status (last)",
                ],
            },
            {
                "name": "fresh-process restoration certification",
                "physical_launches": 9,
                "launch_reason": (
                    "three seed-prefix repetitions for each of two episodes, "
                    "plus three direct-checkpoint repetitions"
                ),
                "game_resets": 9,
            },
            {
                "name": "branch restoration",
                "physical_launches": 1,
                "launch_reason": "explicit child branch launch",
                "game_resets": 1,
            },
        ]
    return {
        "stages": stages,
        "expected_physical_launches": sum(stage["physical_launches"] for stage in stages),
        "expected_game_resets": sum(stage["game_resets"] for stage in stages),
        "native_evidence_collected": False,
        "release_certification_requested": not gameplay_only,
        "capability_activation_requested": False,
    }


def collect_functional_cases(
    config,
    game_factory,
    *,
    smoke_config=None,
    include_invalid=True,
    include_actions=True,
):
    """Run ordinary gameplay cases through one caller-owned process factory."""
    smoke_config = smoke_config or load_config(ROOT / "configs/smoke.yaml")
    results = {}
    if include_invalid:
        results["invalid"] = invalid_tests(smoke_config, game_factory=game_factory)
    if include_actions:
        results["actions"] = exercise_shop(config, game_factory=game_factory)
    results["win"] = exercise_win(config, game_factory=game_factory)
    results["runs"] = ordinary_runs(game_factory=game_factory)
    results["reorder"] = exercise_reorder(smoke_config, game_factory=game_factory)
    results["faults"] = fault_tests(smoke_config, game_factory=game_factory)
    return results


def collect_profiles_and_functionals(configs, session_factory, functional_collector):
    """Use two fresh profile processes, then retain the second for gameplay cases."""
    with session_factory(configs[0].environment, reason="startup") as first_session:
        first = audit_session(configs, first_session.new_game, persist_rules=True)
    with session_factory(configs[0].environment, reason="startup") as second_session:
        second = audit_session(configs, second_session.new_game, persist_rules=False)
        profiles = write_profile_audits(configs, [first, second])
        functional = functional_collector(second_session)
    return profiles, functional


def collect_gameplay_only(config, smoke_config, session_factory):
    """Run gameplay collection in one owned process without restoration certification."""
    with session_factory(smoke_config.environment, reason="startup") as native_session:
        return collect_functional_cases(
            config,
            native_session.new_game,
            smoke_config=smoke_config,
        )


def main(*, session_factory=NativeSession):
    parser = argparse.ArgumentParser(description=__doc__)
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument(
        "--resume-certification",
        action="store_true",
        help="Reuse completed native collection for the same pinned runtime, then repeat certification and branching.",
    )
    resume.add_argument(
        "--resume-actions",
        help="Reuse a completed action-fixture episode after a later collection startup failure",
    )
    resume.add_argument(
        "--gameplay-only",
        action="store_true",
        help="Collect gameplay cases in one session without fresh-process restoration certification.",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print expected stages, game resets, and physical launches without touching native inputs.",
    )
    args = parser.parse_args()
    if args.plan:
        print(
            json.dumps(
                release_plan(
                    resume_certification=args.resume_certification,
                    resume_actions=bool(args.resume_actions),
                    gameplay_only=args.gameplay_only,
                ),
                indent=2,
            )
        )
        return
    store = Store(ROOT / "data")
    source = implementation_fingerprint()
    config = load_config(ROOT / "configs/pilot.yaml")
    if args.gameplay_only:
        smoke_config = load_config(ROOT / "configs/smoke.yaml")
        functional = collect_gameplay_only(config, smoke_config, session_factory)
        environment_hash = digest(json.loads((ROOT / "private/environment.lock.json").read_text()))
        result = {
            "evidence_scope": "gameplay_collection_only",
            "evidence_kind": "NATIVE_CALIBRATION",
            "native_release_evidence": False,
            "capability_certificates_created": False,
            "implementation_hash_at_start": source,
            "implementation_hash_after_collection": implementation_fingerprint(),
            "environment_hash": environment_hash,
            "profile_audit_included": False,
            "restoration_certification_included": False,
            "functional_cases": {
                "invalid_action": functional["invalid"],
                "actions": functional["actions"],
                "terminal_fixture": functional["win"],
                "ordinary_runs": functional["runs"],
                "reorder": functional["reorder"],
                "acknowledgment_faults": functional["faults"],
            },
        }
        path = (
            ROOT
            / "reports/verification"
            / ("native-gameplay-collection-" + uuid.uuid4().hex + ".json")
        )
        atomic_json(path, result, immutable=True)
        print(json.dumps({"collection_only_artifact": str(path), "result": result}), flush=True)
        return
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
        with session_factory(
            load_config(ROOT / "configs/smoke.yaml").environment,
            reason="startup",
        ) as native_session:
            exercise_reorder(
                load_config(ROOT / "configs/smoke.yaml"),
                game_factory=native_session.new_game,
            )
    else:
        if args.resume_actions:
            smoke_config = load_config(ROOT / "configs/smoke.yaml")
            current_environment = digest(
                json.loads((ROOT / "private/environment.lock.json").read_text())
            )
            for stake in ("WHITE", "GOLD"):
                audit = json.loads(
                    (ROOT / f"reports/verification/runtime-audit-{stake}.json").read_text()
                )
                if audit["environment_hash"] != current_environment or not audit["profile_stable"]:
                    raise ValueError("PRIOR_PROFILE_COLLECTION_INVALID")
            invalid = json.loads((ROOT / "reports/verification/native-invalid.json").read_text())
            if not invalid["unchanged"]:
                raise ValueError("PRIOR_INVALID_ACTION_COLLECTION_INVALID")
            initial = json.loads(
                (store.episode_path(invalid["episode_id"], True) / "checkpoint-0.json").read_text()
            )
            if digest(initial["game"]["environment"]) != current_environment:
                raise ValueError("PRIOR_INVALID_ACTION_ENVIRONMENT_CHANGED")
            fixture = completed_action_fixture(store, args.resume_actions, current_environment)
            with session_factory(
                load_config(ROOT / "configs/smoke.yaml").environment,
                reason="startup",
            ) as native_session:
                functional = collect_functional_cases(
                    config,
                    native_session.new_game,
                    smoke_config=smoke_config,
                    include_invalid=False,
                    include_actions=False,
                )
            win = functional["win"]
        else:
            profile_configs = [
                load_config(ROOT / "configs/smoke.yaml"),
                config,
            ]
            _, functional = collect_profiles_and_functionals(
                profile_configs,
                session_factory,
                lambda native_session: collect_functional_cases(
                    config,
                    native_session.new_game,
                    smoke_config=profile_configs[0],
                ),
            )
            fixture = functional["actions"]
            win = functional["win"]
        atomic_json(
            ROOT / "reports/verification/native-fixtures-final.json",
            {"actions": fixture, "win": win},
        )
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
    command("verify_settlement_evidence.py")
    print(
        json.dumps({"native_release_evidence": True, "branch": child, "parent": gold}), flush=True
    )


if __name__ == "__main__":
    main()
