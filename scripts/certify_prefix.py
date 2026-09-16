#!/usr/bin/env python3
"""Verify a complete seed replay and certify boundaries traversed in that same proof."""

import argparse
import json
import uuid

from balatro_horizons.config import ROOT, Config
from balatro_horizons.engine.certification import (
    certificate_path,
    read_checkpoint,
    steps_for,
    verify_checkpoint,
)
from balatro_horizons.storage.journal import Store, atomic_json, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode_id")
    args = parser.parse_args()
    store = Store(ROOT / "data")
    config = Config.model_validate(store.manifest(args.episode_id, True)["config"])
    cert = verify_checkpoint(store, config, args.episode_id, 0, mode="seed_prefix")
    print(json.dumps(cert), flush=True)
    if cert["status"] != "passed":
        raise SystemExit(1)
    steps = steps_for(store, args.episode_id)
    # Each boundary was compared during all three complete seed-prefix/suffix passes.
    # No new untested native-save capability is inferred from this proof.
    for path in sorted(store.episode_path(args.episode_id, True).glob("checkpoint-*.json")):
        decision = int(path.stem.split("-")[1])
        suffix = [s for s in steps if s["observation"]["observation_id"] > decision]
        if decision == 0 or not any(s["kind"] == "action" for s in suffix):
            continue
        checkpoint = read_checkpoint(store, args.episode_id, decision)
        derived = {
            **cert,
            "certificate_id": uuid.uuid4().hex,
            "decision": decision,
            "phase": checkpoint["observation"]["phase"],
            "checkpoint_hash": digest(checkpoint),
            "suffix_hash": digest(suffix),
            "derived_from": cert["certificate_id"],
            "derivation": "boundary traversed and compared in three complete seed replays",
        }
        target = certificate_path(store, args.episode_id, decision)
        atomic_json(
            target.with_name("certificate-record-" + derived["certificate_id"] + ".json"),
            derived,
            immutable=True,
        )
        atomic_json(target, derived)
        print(
            json.dumps(
                {
                    "decision": decision,
                    "phase": derived["phase"],
                    "mode": "seed_prefix",
                    "status": "passed",
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
