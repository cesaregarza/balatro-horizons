"""Offline proofs for extending a certificate from measured configuration starts."""

import json
from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT, Environment
from balatro_horizons.evidence import certification, configurations, provenance, reuse
from balatro_horizons.storage.journal import digest


@pytest.fixture
def coverage(tmp_path, monkeypatch):
    root = tmp_path / "candidate"
    source = provenance.source_files(ROOT)
    for name, content in source.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (root / "src/balatro_horizons/run_options.json").write_bytes(
        (ROOT / "src/balatro_horizons/run_options.json").read_bytes()
    )
    lock = {"environment": "test"}
    old = {
        "certificate_id": "a" * 32, "status": "passed", "created_at": "test",
        "implementation_hash": provenance.fingerprint_sources(source),
        "environment_hash": digest(lock), "configurations": [["RED", "GOLD"]],
        "profile_hashes": {"RED/GOLD": digest({"profile": "measured"})}, "phases": ["BLIND_SELECT"],
    }
    (root / "private").mkdir()
    for name, value in (("capability-certificate.json", old), ("capability-record-" + old["certificate_id"] + ".json", old), ("environment.lock.json", lock)):
        (root / "private" / name).write_text(json.dumps(value))
    (root / "reports/verification").mkdir(parents=True)
    (root / "reports/verification/native-evidence.json").write_text(json.dumps({
        "implementation_hash": old["implementation_hash"], "environment_hash": digest(lock),
    }))
    changed = root / "src/balatro_horizons/evidence/configurations.py"
    changed.write_bytes(changed.read_bytes() + b"\n# Tested candidate evidence change.\n")
    candidate_hash = provenance.fingerprint_sources(provenance.source_files(root))
    offline = tmp_path / "offline.json"
    offline.write_text(json.dumps({"status": "passed", "suite": "check_offline", "implementation_hash": candidate_hash}))
    monkeypatch.setattr(reuse, "revision_sources", lambda *_: ("b" * 40, source))
    report = {
        "schema_version": 1, "kind": configurations.KIND, "status": "passed",
        "implementation_hash": candidate_hash,
        "native_implementation_hash": provenance.native_implementation_fingerprint(source),
        "environment_hash": digest(lock), "parent_certificate_hash": digest(old),
        "parent_certificate_id": old["certificate_id"], "catalog_hash": configurations.catalog_hash(root),
        "native_launches": 1, "game_resets": 120, "paid_calls": 0, "restoration_certified": False,
        "cases": [
            {"deck": deck, "stake": stake, "observed_deck": deck, "observed_stake": stake,
             "phase": "BLIND_SELECT", "profile_hash": digest({"profile": "measured"}), "checks": configurations.CHECKS}
            for deck, stake in configurations.pairs()
        ],
    }
    path = tmp_path / "configuration-report.json"
    path.write_text(json.dumps(report))
    return root, path, offline, old, report


def test_dry_run_never_changes_selection_and_apply_keeps_history(coverage, monkeypatch):
    root, path, offline, old, _ = coverage
    before = (root / "private/capability-certificate.json").read_bytes()
    plan = configurations.accept(root, "baseline", path, offline)
    assert plan == {"compatible": True, "activated": False, "configurations": 120,
                    "native_launches": 0, "checkpoint_certificates_migrated": False,
                    "scope": "initial_configuration_and_profile"}
    assert (root / "private/capability-certificate.json").read_bytes() == before
    assert configurations.accept(root, "baseline", path, offline, apply=True)["activated"]
    active = json.loads((root / "private/capability-certificate.json").read_text())
    assert len(active["configurations"]) == len(active["profile_hashes"]) == 120
    assert active["phases"] == old["phases"]
    assert active["implementation_hash"] == old["implementation_hash"]
    assert active["accepted_implementation_hash"] != old["implementation_hash"]
    assert active["configuration_evidence"]["restoration_certified"] is False
    original = root / "private" / ("capability-record-" + old["certificate_id"] + ".json")
    assert json.loads(original.read_text()) == old
    assert (root / active["configuration_evidence"]["artifact"]).is_file()
    monkeypatch.setattr(certification, "ROOT", root)
    monkeypatch.setattr(provenance, "ROOT", root)
    certification.require_environment_certificate({"environment": "test"}, Environment(deck="ERRATIC", stake="ORANGE"))


@pytest.mark.parametrize("field,value", [
    ("status", "failed"), ("implementation_hash", "wrong"), ("native_implementation_hash", "wrong"),
    ("environment_hash", "wrong"), ("catalog_hash", "wrong"), ("parent_certificate_hash", "wrong"),
    ("parent_certificate_id", "wrong"), ("native_launches", 2), ("game_resets", 119),
    ("paid_calls", 1), ("restoration_certified", True),
])
def test_unbound_or_wrong_scope_report_never_activates(coverage, field, value):
    root, path, offline, old, report = coverage
    report[field] = value
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="CONFIGURATION_EVIDENCE_MISMATCH"):
        configurations.accept(root, "baseline", path, offline, apply=True)
    assert json.loads((root / "private/capability-certificate.json").read_text()) == old


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_deck", "not_ready", "no_checks", "invalid_profile", "changed_profile"])
def test_each_measured_case_is_required(coverage, mutation):
    root, path, offline, old, report = coverage
    if mutation == "missing":
        report["cases"].pop()
    elif mutation == "duplicate":
        report["cases"][-1] = deepcopy(report["cases"][0])
    else:
        row = report["cases"][7]  # Already certified Red/Gold must not change profile.
        key, value = {
            "wrong_deck": ("observed_deck", "BLUE"), "not_ready": ("phase", "MENU"),
            "no_checks": ("checks", []), "invalid_profile": ("profile_hash", "unknown"),
            "changed_profile": ("profile_hash", "b" * 64),
        }[mutation]
        row[key] = value
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        configurations.accept(root, "baseline", path, offline, apply=True)
    assert json.loads((root / "private/capability-certificate.json").read_text()) == old


def test_configuration_extension_does_not_bypass_native_or_offline_identity(coverage):
    root, path, offline, _, _ = coverage
    native = root / "src/balatro_horizons/game/session.py"
    native.write_bytes(native.read_bytes() + b"\n# Changed native execution.\n")
    with pytest.raises(ValueError, match="NATIVE_GAME_SOURCE_CHANGED"):
        configurations.accept(root, "baseline", path, offline, apply=True)
