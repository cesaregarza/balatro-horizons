"""Publish native capability only from current completed evidence artifacts."""

import html
import json
import uuid
from pathlib import Path

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.evaluation.reports import episode_export, scan
from balatro_horizons.evidence.certification import require_checkpoint_certificate
from balatro_horizons.evidence.lock import read_with_digest
from balatro_horizons.evidence.provenance import (
    implementation_fingerprint,
    native_implementation_fingerprint,
)
from balatro_horizons.evidence.validation import require
from balatro_horizons.game.session import WindowsBridge
from balatro_horizons.storage.journal import Store, atomic_json, digest, now


def _read(root: Path, name: str) -> dict:
    return json.loads((root / "reports/verification" / name).read_text())


def _verified_lock(root: Path):
    lock, lock_hash = read_with_digest(root)
    config_path = root / "configs/pilot.yaml"
    config = load_config(config_path if config_path.is_file() else None)
    verified = WindowsBridge(config.environment).verify_files()
    require(digest(verified) == lock_hash, "NATIVE_ENVIRONMENT_LOCK_CHANGED")
    return lock, lock_hash


def _validate_release(root: Path, release: dict, lock_hash: str, source: str):
    require(release["environment_hash"] == lock_hash, "STALE_NATIVE_ENVIRONMENT")
    require(release["implementation_hash"] == source, "STALE_NATIVE_SOURCE")
    settlement = _read(root, "native-settlement.json")
    require(settlement["status"] == "passed", "MISSING_SETTLEMENT_EVIDENCE")
    require(settlement["implementation_hash"] == source, "STALE_SETTLEMENT_SOURCE")
    require(settlement["environment_hash"] == lock_hash, "STALE_SETTLEMENT_ENVIRONMENT")
    reorder = _read(root, "native-reorder.json")
    require(reorder.get("status") == "passed", "MISSING_REORDER_REGRESSION")
    require(reorder.get("implementation_hash") == source, "STALE_REORDER_SOURCE")
    require(reorder.get("environment_hash") == lock_hash, "STALE_REORDER_ENVIRONMENT")
    required = {
        "BLIND_SELECT:jokers", "BLIND_SELECT:consumables", "ROUND_EVAL:jokers",
        "ROUND_EVAL:consumables", "duplicate_request_unchanged",
    }
    require(required <= set(reorder["checks"]), "INCOMPLETE_REORDER_REGRESSION")
    return reorder


def _validate_functional(root: Path, store, release: dict, lock_hash: str):
    required_actions = {
        "select_blind", "skip_blind", "play_hand", "discard", "reorder", "buy", "sell",
        "use_consumable", "reroll_shop", "reroll_boss", "choose_pack", "skip_pack",
        "cash_out", "leave_shop",
    }
    require(required_actions <= set(release["fixture"]["actions"]), "INCOMPLETE_ACTION_EVIDENCE")
    require(release["win_fixture"]["outcome"] == "WIN", "MISSING_NATIVE_WIN_FIXTURE")
    require(_read(root, "native-invalid.json")["unchanged"], "INVALID_ACTION_MUTATED_STATE")
    faults = _read(root, "native-faults.json")["tests"]
    require(len(faults) == 2 and all(row["duplicate_had_no_effect"] for row in faults),
            "MISSING_TIMEOUT_EVIDENCE")
    require({row["outcome"] for row in faults} == {"GAME_LOSS", "INFRASTRUCTURE_FAILURE"},
            "TIMEOUT_OUTCOMES_INVALID")
    profiles = {}
    for preset, stake in (("smoke", "WHITE"), ("pilot", "GOLD")):
        audit = _read(root, f"runtime-audit-{stake}.json")
        require(audit["profile_stable"] and audit["fresh_processes"] >= 2, "PROFILE_DRIFT")
        require(audit["environment_hash"] == lock_hash, "STALE_PROFILE_EVIDENCE")
        summary = release["ordinary_runs"][preset]
        require(summary["outcome"] in ("WIN", "GAME_LOSS") and summary["provider_calls"] == 0,
                "INCOMPLETE_ORDINARY_RUN")
        eid = summary["episode_id"]
        require(store.summary(eid)["journal_head"] == summary["journal_head"], "RUN_JOURNAL_CHANGED")
        raw = json.loads((store.episode_path(eid, True) / "raw-0.json").read_text())
        profiles["RED/" + stake] = digest(raw["raw_engine"]["bh"]["profile"])
        require(profiles["RED/" + stake] == audit["profile_hashes"][0], "INITIAL_PROFILE_MISMATCH")
    return profiles


def _validate_certificates(store, release: dict):
    gold = release["parent_episode_id"]
    require_checkpoint_certificate(store, gold, 0)
    phases = set()
    fixture = release["fixture"]
    for phase, decision in fixture["checkpoints"].items():
        if phase == "SELECTING_HAND":
            continue
        _, certificate = require_checkpoint_certificate(store, fixture["episode_id"], decision)
        require(certificate["mode"] == "seed_prefix", "WRONG_RESTORATION_EVIDENCE")
        phases.add(phase)
    _, hand = require_checkpoint_certificate(store, gold, 1)
    phases.add(hand["phase"])
    child = release["branch"]["episode_id"]
    require(release["parent_unchanged"] and store.manifest(child)["parent_episode_id"] == gold,
            "BRANCH_ANCESTRY_INVALID")
    require(not store.manifest(child)["evaluation_eligible"], "ASSISTED_BRANCH_SCORED")
    require(store.summary(child)["outcome"] in ("WIN", "GAME_LOSS"), "BRANCH_NOT_COMPLETE")
    return gold, child, phases


def _certificate(lock_hash: str, source: str, native_hash: str, profiles: dict, phases: set):
    return {
        "schema_version": 2, "certificate_id": uuid.uuid4().hex, "status": "passed",
        "created_at": now(), "environment_hash": lock_hash, "implementation_hash": source,
        "accepted_implementation_hash": source, "native_implementation_hash": native_hash,
        "validation_kind": "native_suite", "configurations": [["RED", "WHITE"], ["RED", "GOLD"]],
        "profile_hashes": profiles, "phases": sorted(phases), "headless": False,
        "accelerated": False, "paid_provider_validation": False,
        "evidence": "reports/verification/native-release.json",
    }


def _public(store, lock: dict, gold: str, child: str):
    provenance = {
        key: lock[key]
        for key in (
            "game_version", "game_sha256", "lovely_version", "injector_sha256",
            "balatrobot_commit", "steamodded_release", "steamodded_commit",
            "mods_sha256", "bridge_sha256",
        )
    }
    result = {
        "provenance": provenance,
        "interpretation": "Native calibration and assisted diagnostic evidence, not a model comparison or an estimate of win probability.",
        "run": episode_export(store, gold),
        "branch": episode_export(store, child),
    }
    scan(result, [store.manifest(gold, True).get("seed"), store.manifest(child, True).get("seed")])
    return result


def publish(root: Path | None = None):
    """Validate reports and publish the capability selection last."""
    root = Path(ROOT if root is None else root).resolve()
    store = Store(root / "data")
    release = _read(root, "native-release.json")
    lock, lock_hash = _verified_lock(root)
    source = implementation_fingerprint()
    _validate_release(root, release, lock_hash, source)
    profiles = _validate_functional(root, store, release, lock_hash)
    gold, child, phases = _validate_certificates(store, release)
    native_hash = native_implementation_fingerprint()
    certificate = _certificate(lock_hash, source, native_hash, profiles, phases)
    evidence = {
        "environment_hash": lock_hash, "implementation_hash": source,
        "accepted_implementation_hash": source, "native_implementation_hash": native_hash,
    }
    artifacts = [
        "reports/verification/native-release.json", "reports/verification/native-fixtures-final.json",
        "reports/verification/native-reorder.json", "reports/verification/native-settlement.json",
        "reports/verification/native-invalid.json", "reports/verification/native-faults.json",
        "reports/verification/runtime-audit-WHITE.json", "reports/verification/runtime-audit-GOLD.json",
    ]
    for key in ("AT-04", "AT-05", "AT-07", "AT-08", "AT-09", "AT-10", "AT-11", "AT-12", "AT-23"):
        evidence[key] = {"status": "passed", "evidence_kind": "NATIVE", "artifacts": artifacts}
    public = _public(store, lock, gold, child)
    atomic_json(root / "reports/verification/public-native-diagnostic.json", public)
    summary = {
        "ordinary_runs": release["ordinary_runs"], "branch": release["branch"],
        "certified_phases": sorted(phases),
        "restoration": "seed-prefix; direct saves only where individually certified",
        "paid_calls": 0, "evidence_kind": "NATIVE_CALIBRATION", "evaluation_eligible": False,
    }
    template = (Path(__file__).parent / "templates/native-diagnostic.html").read_text()
    html_report = template.replace("{{SUMMARY_JSON}}", html.escape(json.dumps(summary, indent=2)))
    (root / "reports/verification/native-diagnostic-report.html").write_text(html_report)
    atomic_json(root / "reports/verification/native-diagnostic-report.json", summary)
    _publish_gate(root, certificate, evidence)
    return {"native_capability": "passed", "configurations": certificate["configurations"],
            "phases": sorted(phases), "paid_calls": 0}


def _publish_gate(root: Path, certificate: dict, evidence: dict):
    """Write immutable evidence before changing the mutable selection pointer."""
    atomic_json(root / "private" / ("capability-record-" + certificate["certificate_id"] + ".json"),
                certificate, immutable=True)
    atomic_json(root / "reports/verification/native-evidence.json", evidence)
    atomic_json(root / "private/capability-certificate.json", certificate)
