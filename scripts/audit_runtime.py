#!/usr/bin/env python3
"""Check fresh-profile stability and freeze licensed local rules without exporting them."""

import argparse
import json
import uuid

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.native import NativeGame
from balatro_horizons.storage.journal import atomic_json, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=["smoke", "pilot"], default="smoke")
    args = parser.parse_args()
    config = load_config(ROOT / f"configs/{args.preset}.yaml")
    seed = uuid.uuid4().hex[:8].upper()
    profiles = []
    for repetition in range(2):
        game = NativeGame(config.environment, seed, calibration=True)
        try:
            game.wait_ready()
            profiles.append(digest(game.raw["bh"]["profile"]))
            if repetition == 0:
                entries = game.bridge.rpc("bh_rules")["rules"]
                aliases = {}
                for key, entry in entries.items():
                    name = entry.get("name")
                    if isinstance(name, str):
                        aliases[name.casefold()] = key
                rules = {
                    "entries": entries,
                    "aliases": aliases,
                    "environment_hash": digest(game.lock),
                }
                atomic_json(ROOT / "private/rules.json", rules)
        finally:
            game.close()
    result = {
        "fresh_processes": 2,
        "profile_hashes": profiles,
        "profile_stable": len(set(profiles)) == 1,
        "rule_entries": len(entries),
        "rules_hash": digest(rules),
        "environment_hash": digest(game.lock),
        "identity": "BalatroHorizons",
    }
    atomic_json(
        ROOT / "reports/verification" / f"runtime-audit-{config.environment.stake}.json", result
    )
    if config.environment.stake == "WHITE":
        atomic_json(ROOT / "reports/verification/runtime-audit.json", result)
    print(json.dumps(result))
    if not result["profile_stable"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
