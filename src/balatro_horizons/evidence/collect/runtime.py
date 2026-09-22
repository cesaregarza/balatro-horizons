"""Collect fresh-process profile and licensed-rule stability evidence."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from balatro_horizons.config import ROOT
from balatro_horizons.game.contract import EvaluatorSession
from balatro_horizons.storage.journal import atomic_json, digest

GameFactory = Callable[[Any, str], EvaluatorSession]


def audit_session(
    configs: Sequence[Any], game_factory: GameFactory, *, persist_rules: bool = True
) -> dict[str, dict[str, Any]]:
    """Read one private profile/rules sample per stake from an owned process."""
    samples: dict[str, dict[str, Any]] = {}
    for config in configs:
        seed = uuid.uuid4().hex[:8].upper()
        session = game_factory(config.environment, seed)
        try:
            session.wait_ready()
            profile = session.inspect_raw()["bh"]["profile"]
            entries = session.rules()["rules"]
            aliases = {
                entry["name"].casefold(): key
                for key, entry in entries.items()
                if isinstance(entry.get("name"), str)
            }
            rules = {
                "entries": entries,
                "aliases": aliases,
                "environment_hash": digest(session.lock),
            }
            if persist_rules:
                atomic_json(ROOT / "private/rules.json", rules)
            samples[config.environment.stake] = {
                "profile_hash": digest(profile),
                "rule_entries": len(entries),
                "rules_hash": digest(rules),
                "environment_hash": digest(session.lock),
                "identity": "BalatroHorizons",
            }
        finally:
            session.close()
    return samples


def write_profile_audits(configs: Sequence[Any], process_samples: Sequence[dict]) -> dict:
    """Persist the stable per-stake report shape from fresh-process samples."""
    if not process_samples:
        raise ValueError("PROFILE_AUDIT_REQUIRES_PROCESS_SAMPLES")
    results = {}
    for config in configs:
        stake = config.environment.stake
        samples = [sample[stake] for sample in process_samples]
        hashes = [sample["profile_hash"] for sample in samples]
        environments = {sample["environment_hash"] for sample in samples}
        if len(environments) != 1:
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
        report_root = ROOT / "reports/verification"
        atomic_json(report_root / f"runtime-audit-{stake}.json", result)
        if stake == "WHITE":
            atomic_json(report_root / "runtime-audit.json", result)
    if not all(result["profile_stable"] for result in results.values()):
        raise SystemExit(1)
    return results
