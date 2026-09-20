#!/usr/bin/env python3
"""Check fresh-profile stability and freeze licensed local rules without exporting them."""

import argparse
import uuid

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.game.session import NativeSession
from balatro_horizons.storage.journal import atomic_json, digest


def audit_session(configs, game_factory, *, persist_rules=True):
    """Read one profile sample per stake from a caller-owned fresh process."""
    samples = {}
    for config in configs:
        seed = uuid.uuid4().hex[:8].upper()
        game = game_factory(config.environment, seed)
        try:
            game.wait_ready()
            profile_hash = digest(game.inspect_raw()["bh"]["profile"])
            entries = game.rules()["rules"]
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
            if persist_rules:
                atomic_json(ROOT / "private/rules.json", rules)
            samples[config.environment.stake] = {
                "profile_hash": profile_hash,
                "rule_entries": len(entries),
                "rules_hash": digest(rules),
                "environment_hash": digest(game.lock),
                "identity": "BalatroHorizons",
            }
        finally:
            game.close()
    return samples


def write_profile_audits(configs, process_samples):
    """Write the historical per-stake report shape from fresh-process samples."""
    if not process_samples:
        raise ValueError("PROFILE_AUDIT_REQUIRES_PROCESS_SAMPLES")
    results = {}
    for config in configs:
        stake = config.environment.stake
        samples = [sample[stake] for sample in process_samples]
        hashes = [sample["profile_hash"] for sample in samples]
        if len({sample["environment_hash"] for sample in samples}) != 1:
            raise ValueError("PROFILE_AUDIT_ENVIRONMENT_CHANGED")
        result = {
            "fresh_processes": len(process_samples),
            "profile_hashes": hashes,
            "profile_stable": len(set(hashes)) == 1,
            "rule_entries": samples[0]["rule_entries"],
            "rules_hash": samples[0]["rules_hash"],
            "environment_hash": samples[0]["environment_hash"],
            "identity": samples[0]["identity"],
        }
        results[stake] = result
        atomic_json(ROOT / "reports/verification" / f"runtime-audit-{stake}.json", result)
        if stake == "WHITE":
            atomic_json(ROOT / "reports/verification/runtime-audit.json", result)
    if not all(result["profile_stable"] for result in results.values()):
        raise SystemExit(1)
    return results


def collect_profile_audits(configs, *, session_factory=NativeSession):
    """Run two genuinely fresh process repetitions, auditing all requested stakes."""
    process_samples = []
    for repetition in range(2):
        with session_factory(
            configs[0].environment,
            reason="startup",
        ) as session:
            process_samples.append(
                audit_session(
                    configs,
                    session.new_game,
                    persist_rules=repetition == 0,
                )
            )
    return write_profile_audits(configs, process_samples)


def main(*, session_factory=NativeSession):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=["smoke", "pilot"], default="smoke")
    args = parser.parse_args()
    config = load_config(ROOT / f"configs/{args.preset}.yaml")
    return collect_profile_audits([config], session_factory=session_factory)


if __name__ == "__main__":
    main()
