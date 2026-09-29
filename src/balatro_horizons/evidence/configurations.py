"""Extend configuration coverage from measured starts, never inferred profiles."""

import hashlib
import json
import re
from itertools import product
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import reuse
from balatro_horizons.evidence.lock import native_path
from balatro_horizons.evidence.validation import require
from balatro_horizons.run_configuration import OPTIONS
from balatro_horizons.storage.journal import atomic_json, digest

KIND = "native_configuration_startup_v1"
CHECKS = ["selected_configuration", "ready_blind_select", "public_observation", "profile_captured"]


def pairs():
    return list(product(OPTIONS["decks"], OPTIONS["stakes"]))


def catalog_hash(root=ROOT):
    return hashlib.sha256((root / "src/balatro_horizons/run_options.json").read_bytes()).hexdigest()


def report_profiles(report, certificate, root):
    require(isinstance(report, dict), "CONFIGURATION_EVIDENCE_MISMATCH")
    expected = {
        "schema_version": 1, "kind": KIND, "status": "passed",
        "implementation_hash": certificate["accepted_implementation_hash"],
        "native_implementation_hash": certificate["native_implementation_hash"],
        "environment_hash": certificate["environment_hash"],
        "parent_certificate_hash": certificate["reuse"]["parent_certificate_hash"],
        "parent_certificate_id": certificate["reuse"]["parent_certificate_id"],
        "catalog_hash": catalog_hash(root), "native_launches": 1,
        "game_resets": len(pairs()), "paid_calls": 0, "restoration_certified": False,
    }
    require(all(report.get(key) == value for key, value in expected.items()),
            "CONFIGURATION_EVIDENCE_MISMATCH")
    rows = report.get("cases", [])
    require(isinstance(rows, list) and len(rows) == len(pairs()), "INCOMPLETE_CONFIGURATION_EVIDENCE")
    profiles = {}
    for row in rows:
        require(isinstance(row, dict), "INVALID_CONFIGURATION_CASE")
        key = (row.get("deck"), row.get("stake"))
        require(key in pairs() and "/".join(key) not in profiles, "INVALID_CONFIGURATION_CASE")
        require((row.get("observed_deck"), row.get("observed_stake")) == key
                and row.get("phase") == "BLIND_SELECT" and row.get("checks") == CHECKS,
                "CONFIGURATION_START_NOT_VERIFIED")
        profile = row.get("profile_hash")
        require(isinstance(profile, str) and re.fullmatch(r"[a-f0-9]{64}", profile),
                "INVALID_CONFIGURATION_PROFILE")
        profiles["/".join(key)] = profile
    for key, profile in certificate.get("profile_hashes", {}).items():
        if key in profiles:
            require(profiles[key] == profile, "CERTIFIED_PROFILE_CHANGED")
    return profiles


def prepare(root: Path, baseline: str, report_path: Path, offline_report: Path):
    root = native_path(root)
    certificate, evidence = reuse.prepare(root, root, baseline, offline_report)
    report = json.loads(native_path(report_path).read_text())
    profiles = report_profiles(report, certificate, root)
    artifact = "reports/verification/configurations-" + digest(report) + ".json"
    coverage = {"kind": KIND, "artifact": artifact, "report_hash": digest(report),
                "scope": "initial_configuration_and_profile", "restoration_certified": False}
    certificate = {
        **certificate, "validation_kind": "configuration_extension",
        "configurations": sorted(certificate["configurations"] + [
            list(pair) for pair in pairs() if list(pair) not in certificate["configurations"]
        ]),
        "profile_hashes": {**certificate.get("profile_hashes", {}), **profiles},
        "configuration_evidence": coverage,
    }
    evidence = {**evidence, "configuration_startup": {
        "status": "passed", "evidence_kind": "NATIVE", "artifacts": [artifact], **coverage,
    }}
    return certificate, evidence, report


def accept(root, baseline, report_path, offline_report, *, apply=False):
    root = native_path(root)
    certificate, evidence, report = prepare(root, baseline, report_path, offline_report)
    if apply:
        artifact = root / certificate["configuration_evidence"]["artifact"]
        if artifact.exists():
            require(json.loads(artifact.read_text()) == report, "CONFIGURATION_ARTIFACT_CHANGED")
        else:
            atomic_json(artifact, report, immutable=True)
        reuse.activate(root, root, certificate, evidence)
    return {"compatible": True, "activated": apply, "configurations": len(certificate["configurations"]),
            "native_launches": 0, "checkpoint_certificates_migrated": False,
            "scope": "initial_configuration_and_profile"}
