#!/usr/bin/env python3
"""Activate native capability only from current, completed verification artifacts."""

import html
import json
import uuid

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.certification import require_checkpoint_certificate
from balatro_horizons.engine.native import WindowsBridge
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.evaluation.reports import episode_export, scan
from balatro_horizons.storage.journal import Store, atomic_json, digest, now


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read(name):
    return json.loads((ROOT / "reports/verification" / name).read_text())


def main():
    store = Store(ROOT / "data")
    release = read("native-release.json")
    lock = WindowsBridge(load_config().environment).verify_files()
    source = implementation_fingerprint()
    require(release["environment_hash"] == digest(lock), "STALE_NATIVE_ENVIRONMENT")
    require(release["implementation_hash"] == source, "STALE_NATIVE_SOURCE")
    reorder = read("native-reorder.json")
    require(reorder.get("status") == "passed", "MISSING_REORDER_REGRESSION")
    require(reorder.get("implementation_hash") == source, "STALE_REORDER_SOURCE")
    require(reorder.get("environment_hash") == digest(lock), "STALE_REORDER_ENVIRONMENT")
    require(
        {
            "BLIND_SELECT:jokers",
            "BLIND_SELECT:consumables",
            "ROUND_EVAL:jokers",
            "ROUND_EVAL:consumables",
            "duplicate_request_unchanged",
        }
        <= set(reorder["checks"]),
        "INCOMPLETE_REORDER_REGRESSION",
    )
    required_actions = {
        "select_blind",
        "skip_blind",
        "play_hand",
        "discard",
        "reorder",
        "buy",
        "sell",
        "use_consumable",
        "reroll_shop",
        "reroll_boss",
        "choose_pack",
        "skip_pack",
        "cash_out",
        "leave_shop",
    }
    require(required_actions <= set(release["fixture"]["actions"]), "INCOMPLETE_ACTION_EVIDENCE")
    require(release["win_fixture"]["outcome"] == "WIN", "MISSING_NATIVE_WIN_FIXTURE")
    require(read("native-invalid.json")["unchanged"], "INVALID_ACTION_MUTATED_STATE")
    faults = read("native-faults.json")["tests"]
    require(
        len(faults) == 2 and all(x["duplicate_had_no_effect"] for x in faults),
        "MISSING_TIMEOUT_EVIDENCE",
    )
    require(
        {x["outcome"] for x in faults} == {"GAME_LOSS", "INFRASTRUCTURE_FAILURE"},
        "TIMEOUT_OUTCOMES_INVALID",
    )
    profiles = {}
    for preset, stake in [("smoke", "WHITE"), ("pilot", "GOLD")]:
        audit = read(f"runtime-audit-{stake}.json")
        require(audit["profile_stable"] and audit["fresh_processes"] >= 2, "PROFILE_DRIFT")
        require(audit["environment_hash"] == digest(lock), "STALE_PROFILE_EVIDENCE")
        summary = release["ordinary_runs"][preset]
        require(
            summary["outcome"] in ("WIN", "GAME_LOSS") and summary["provider_calls"] == 0,
            "INCOMPLETE_ORDINARY_RUN",
        )
        eid = summary["episode_id"]
        require(
            store.summary(eid)["journal_head"] == summary["journal_head"], "RUN_JOURNAL_CHANGED"
        )
        raw = json.loads((store.episode_path(eid, True) / "raw-0.json").read_text())
        profiles["RED/" + stake] = digest(raw["raw_engine"]["bh"]["profile"])
        require(profiles["RED/" + stake] == audit["profile_hashes"][0], "INITIAL_PROFILE_MISMATCH")
    gold = release["parent_episode_id"]
    require_checkpoint_certificate(store, gold, 0)
    fixture_id = release["fixture"]["episode_id"]
    phases = set()
    for phase, decision in release["fixture"]["checkpoints"].items():
        if phase == "SELECTING_HAND":
            continue  # This fixture ends after this boundary; use the ordinary run below.
        _, cert = require_checkpoint_certificate(store, fixture_id, decision)
        require(cert["mode"] == "seed_prefix", "WRONG_RESTORATION_EVIDENCE")
        phases.add(phase)
    _, hand_cert = require_checkpoint_certificate(store, gold, 1)
    phases.add(hand_cert["phase"])
    child = release["branch"]["episode_id"]
    require(
        release["parent_unchanged"] and store.manifest(child)["parent_episode_id"] == gold,
        "BRANCH_ANCESTRY_INVALID",
    )
    require(not store.manifest(child)["evaluation_eligible"], "ASSISTED_BRANCH_SCORED")
    require(store.summary(child)["outcome"] in ("WIN", "GAME_LOSS"), "BRANCH_NOT_COMPLETE")
    certificate = {
        "schema_version": 1,
        "certificate_id": uuid.uuid4().hex,
        "status": "passed",
        "created_at": now(),
        "environment_hash": digest(lock),
        "implementation_hash": source,
        "configurations": [["RED", "WHITE"], ["RED", "GOLD"]],
        "profile_hashes": profiles,
        "phases": sorted(phases),
        "headless": False,
        "accelerated": False,
        "paid_provider_validation": False,
        "evidence": "reports/verification/native-release.json",
    }
    evidence = {"environment_hash": digest(lock), "implementation_hash": source}
    artifacts = [
        "reports/verification/native-release.json",
        "reports/verification/native-fixtures-final.json",
        "reports/verification/native-reorder.json",
        "reports/verification/native-invalid.json",
        "reports/verification/native-faults.json",
        "reports/verification/runtime-audit-WHITE.json",
        "reports/verification/runtime-audit-GOLD.json",
    ]
    for key in ("AT-04", "AT-05", "AT-07", "AT-08", "AT-09", "AT-10", "AT-11", "AT-12", "AT-23"):
        evidence[key] = {"status": "passed", "evidence_kind": "NATIVE", "artifacts": artifacts}
    public = {
        "provenance": {
            k: lock[k]
            for k in (
                "game_version",
                "game_sha256",
                "lovely_version",
                "injector_sha256",
                "balatrobot_commit",
                "steamodded_release",
                "steamodded_commit",
                "mods_sha256",
                "bridge_sha256",
            )
        },
        "interpretation": "Native calibration and assisted diagnostic evidence, not a model comparison or an estimate of win probability.",
        "run": episode_export(store, gold),
        "branch": episode_export(store, child),
    }
    scan(public)
    atomic_json(ROOT / "reports/verification/public-native-diagnostic.json", public)
    summary = {
        "ordinary_runs": release["ordinary_runs"],
        "branch": release["branch"],
        "certified_phases": sorted(phases),
        "restoration": "seed-prefix; direct saves only where individually certified",
        "paid_calls": 0,
        "evidence_kind": "NATIVE_CALIBRATION",
        "evaluation_eligible": False,
    }
    atomic_json(ROOT / "reports/verification/native-diagnostic-report.json", summary)
    (ROOT / "reports/verification/native-diagnostic-report.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Balatro Horizons native verification</title>'
        "<style>body{max-width:960px;margin:3rem auto;padding:0 1rem;background:#111b26;color:#edf3fa;font:16px system-ui}pre{white-space:pre-wrap}</style>"
        "<h1>Native pipeline verified</h1><p>Calibration and assisted diagnostics. These runs are excluded from model performance scores.</p>"
        "<pre>" + html.escape(json.dumps(summary, indent=2)) + "</pre>"
    )
    # The active gate is published last: failed exports must leave it unchanged.
    atomic_json(
        ROOT / "private" / ("capability-record-" + certificate["certificate_id"] + ".json"),
        certificate,
        immutable=True,
    )
    atomic_json(ROOT / "reports/verification/native-evidence.json", evidence)
    atomic_json(ROOT / "private/capability-certificate.json", certificate)
    print(
        json.dumps(
            {
                "native_capability": "passed",
                "configurations": certificate["configurations"],
                "phases": sorted(phases),
                "paid_calls": 0,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
