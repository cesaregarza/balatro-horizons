#!/usr/bin/env python3
"""One startup process, two unpaid native runs; no restoration recertification.

Requires task-specific Windows permission. The live worker lock is held throughout.
Use --plan before execution. Copy the existing Linux runtime lock/rules into the
candidate and register its Windows connection before running this script.
"""

import argparse
import fcntl
import json
from pathlib import Path

from native_runs import collect
from reuse_native_evidence import native_path, require, revision_sources
from startup_scope import require_startup_scope

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.native import NativeSession
from balatro_horizons.engine.provenance import fingerprint_sources, native_components, source_files
from balatro_horizons.storage.journal import Store, atomic_json, digest, now


def verify(live, baseline, output):
    live = native_path(live)
    require(not output.exists(), "OUTPUT_EXISTS")
    cert = json.loads((live / "private/capability-certificate.json").read_text())
    _, before = revision_sources(live, baseline)
    after = source_files(ROOT)
    source = fingerprint_sources(after)
    require(cert.get("accepted_implementation_hash", cert["implementation_hash"]) == fingerprint_sources(before), "BASELINE_NOT_CERTIFIED")
    require_startup_scope(before, after)
    lock = json.loads((ROOT / "private/environment.lock.json").read_text())
    require(digest(lock) == cert["environment_hash"], "NATIVE_ENVIRONMENT_CHANGED")
    result = {"suite": "startup_lifecycle", "created_at": now(), "status": "failed",
              "implementation_hash": source, "native_implementation_hash": digest(native_components(after)),
              "environment_hash": digest(lock), "parent_certificate_hash": digest(cert),
              "native_launches": 1, "provider_calls": 0, "cost_usd": 0}
    with (live / "private/native-worker.lock").open("a") as worker:
        fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            cfg = load_config(ROOT / "configs/smoke.yaml")
            with NativeSession(cfg.environment, reason="startup") as session:
                results = collect(game_factory=session.new_game)
            store = Store(ROOT / "data")
            for preset, summary in results.items():
                eid = summary["episode_id"]
                raw = json.loads((store.episode_path(eid, True) / "raw-0.json").read_text())
                stake = "WHITE" if preset == "smoke" else "GOLD"
                require(digest(raw["raw_engine"]["bh"]["profile"]) == cert["profile_hashes"]["RED/" + stake], "FROZEN_PROFILE_MISMATCH")
                require(summary["outcome"] in ("WIN", "GAME_LOSS") and summary["committed_actions"] > 0, "NATIVE_TERMINAL_REQUIRED")
                require(summary["provider_calls"] == 0 and summary["cost_usd"] == 0, "UNPAID_VERIFICATION_REQUIRED")
                require(not store.manifest(eid)["evaluation_eligible"], "CALIBRATION_EVIDENCE_REQUIRED")
            require(fingerprint_sources(source_files(ROOT)) == source, "SOURCE_CHANGED_DURING_VERIFICATION")
            result.update(status="passed", profile_matches=True, runs=results)
        finally:
            atomic_json(output, result, immutable=True)
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-root", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if args.plan:
        print(json.dumps({"native_launches": 1, "reason": "startup and Windows bridge transport",
                          "same_process_runs": ["Red/White", "Red/Gold"], "provider_calls": 0,
                          "restoration_launches": 0, "post_deployment_startup_launches": 1}))
        return
    verify(args.live_root, args.baseline, args.output)


if __name__ == "__main__":
    main()
