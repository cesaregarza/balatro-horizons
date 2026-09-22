"""Declarative native collection coordinator over package-owned collectors."""

import json
import uuid
from pathlib import Path

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.evidence.collect import acceptance, faults, invalid, runs, runtime
from balatro_horizons.evidence.lock import lock_digest
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.evidence.stages import (
    FUNCTIONAL_COLLECTION,
    GAMEPLAY_COLLECTION_ONLY,
    REORDER_ACCEPTANCE_FIXTURE,
    RESUMED_FUNCTIONAL_COLLECTION,
    STARTUP_PROFILE_STABILITY,
)
from balatro_horizons.evidence.stages import from_stage as stage_suffix
from balatro_horizons.game.session import NativeSession
from balatro_horizons.storage.journal import Store, atomic_json, digest


def collect_functional_cases(
    config,
    game_factory,
    *,
    smoke_config=None,
    include_invalid=True,
    include_actions=True,
):
    """Run all functional cases through one caller-owned native process."""
    smoke_config = smoke_config or load_config(ROOT / "configs/smoke.yaml")
    result = {}
    if include_invalid:
        result["invalid"] = invalid.collect(smoke_config, game_factory=game_factory)
    if include_actions:
        result["actions"] = acceptance.exercise_shop(config, game_factory=game_factory)
    result["win"] = acceptance.exercise_win(config, game_factory=game_factory)
    result["runs"] = runs.collect(game_factory=game_factory)
    result["reorder"] = acceptance.exercise_reorder(smoke_config, game_factory=game_factory)
    result["faults"] = faults.collect(smoke_config, game_factory=game_factory)
    return result


def collect_profiles_and_functionals(configs, session_factory, functional_collector):
    """Use two fresh profile processes, retaining the second for functional cases."""
    with session_factory(configs[0].environment, reason="startup") as first_session:
        first = runtime.audit_session(configs, first_session.new_game, persist_rules=True)
    with session_factory(configs[0].environment, reason="startup") as second_session:
        second = runtime.audit_session(configs, second_session.new_game, persist_rules=False)
        profiles = runtime.write_profile_audits(configs, [first, second])
        functional = functional_collector(second_session)
    return profiles, functional


def collect_gameplay_only(config, smoke_config, session_factory):
    """Collect gameplay cases in one process without restoration certification."""
    with session_factory(smoke_config.environment, reason="startup") as native_session:
        return collect_functional_cases(
            config,
            native_session.new_game,
            smoke_config=smoke_config,
        )


def _write_gameplay(root, source, functional):
    current_source = implementation_fingerprint()
    if current_source != source:
        raise ValueError("SOURCE_CHANGED_DURING_COLLECTION")
    result = {
        "evidence_scope": "gameplay_collection_only",
        "evidence_kind": "NATIVE_CALIBRATION",
        "native_release_evidence": False,
        "capability_certificates_created": False,
        "implementation_hash_at_start": source,
        "implementation_hash_after_collection": current_source,
        "environment_hash": lock_digest(root),
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
    path = root / "reports/verification" / f"native-gameplay-collection-{uuid.uuid4().hex}.json"
    atomic_json(path, result, immutable=True)
    return {"collection_only_artifact": str(path), "result": result}


def _collect_functional_stage(root, config, smoke_config, session_factory):
    with session_factory(smoke_config.environment, reason="startup") as session:
        functional = collect_functional_cases(
            config,
            session.new_game,
            smoke_config=smoke_config,
        )
    atomic_json(
        root / "reports/verification/native-fixtures-final.json",
        {"actions": functional["actions"], "win": functional["win"]},
    )
    return functional


def _validate_prior_profiles(root):
    environment_hash = lock_digest(root)
    for stake in ("WHITE", "GOLD"):
        path = root / f"reports/verification/runtime-audit-{stake}.json"
        audit = json.loads(path.read_text())
        if audit["environment_hash"] != environment_hash or not audit["profile_stable"]:
            raise ValueError("PRIOR_PROFILE_COLLECTION_INVALID")
    return environment_hash


def _resume_actions(root, config, smoke, session_factory, episode_id):
    if not episode_id:
        raise ValueError("RESUMED_ACTION_EPISODE_REQUIRED")
    environment_hash = _validate_prior_profiles(root)
    invalid_result = json.loads(
        (root / "reports/verification/native-invalid.json").read_text()
    )
    if not invalid_result["unchanged"]:
        raise ValueError("PRIOR_INVALID_ACTION_COLLECTION_INVALID")
    store = Store(root / "data")
    initial = json.loads(
        (store.episode_path(invalid_result["episode_id"], True) / "checkpoint-0.json").read_text()
    )
    if digest(initial["game"]["environment"]) != environment_hash:
        raise ValueError("PRIOR_INVALID_ACTION_ENVIRONMENT_CHANGED")
    fixture = acceptance.completed_action_fixture(store, episode_id, environment_hash)
    with session_factory(smoke.environment, reason="startup") as session:
        functional = collect_functional_cases(
            config,
            session.new_game,
            smoke_config=smoke,
            include_invalid=False,
            include_actions=False,
        )
    atomic_json(
        root / "reports/verification/native-fixtures-final.json",
        {"actions": fixture, "win": functional["win"]},
    )
    return functional


def _resume_certification(root, smoke, session_factory):
    _validate_prior_profiles(root)
    with session_factory(smoke.environment, reason="startup") as session:
        acceptance.exercise_reorder(smoke, game_factory=session.new_game)
    from balatro_horizons.evidence.release import certify_release

    return certify_release(root)


def collect(*, from_stage=None, gameplay_only=False, action_episode_id=None,
            session_factory=NativeSession, root=ROOT):
    """Execute the requested plan suffix without hidden retries or relaunches."""
    root = Path(root).resolve()
    if from_stage == GAMEPLAY_COLLECTION_ONLY and not gameplay_only:
        raise ValueError("GAMEPLAY_ONLY_REQUIRES_FLAG")
    options = {"gameplay_only": gameplay_only}
    selected = stage_suffix(from_stage, **options) if from_stage and gameplay_only else (
        stage_suffix(from_stage) if from_stage else None
    )
    first = selected[0].name if selected else None
    if selected and selected[0].collector == "certification":
        from balatro_horizons.evidence.release import certify_release

        return certify_release(root, from_stage=first)
    source = implementation_fingerprint()
    config = load_config(root / "configs/pilot.yaml")
    smoke = load_config(root / "configs/smoke.yaml")
    if gameplay_only:
        functional = collect_gameplay_only(config, smoke, session_factory)
        return _write_gameplay(root, source, functional)
    if first == RESUMED_FUNCTIONAL_COLLECTION:
        functional = _resume_actions(
            root, config, smoke, session_factory, action_episode_id
        )
    elif first == REORDER_ACCEPTANCE_FIXTURE:
        return _resume_certification(root, smoke, session_factory)
    elif first == FUNCTIONAL_COLLECTION:
        _validate_prior_profiles(root)
        functional = _collect_functional_stage(root, config, smoke, session_factory)
    else:
        _, functional = collect_profiles_and_functionals(
            [smoke, config],
            session_factory,
            lambda session: collect_functional_cases(
                config,
                session.new_game,
                smoke_config=smoke,
            ),
        )
        atomic_json(
            root / "reports/verification/native-fixtures-final.json",
            {"actions": functional["actions"], "win": functional["win"]},
        )
    current_source = implementation_fingerprint()
    if current_source != source:
        raise ValueError("SOURCE_CHANGED_DURING_COLLECTION")
    return {
        "native_collection": "complete",
        "from_stage": first or STARTUP_PROFILE_STABILITY,
        "implementation_hash_at_start": source,
        "implementation_hash_after_collection": current_source,
        "artifacts": {
            "fixtures": "reports/verification/native-fixtures-final.json",
            "runs": "reports/verification/native-runs.json",
        },
    }
