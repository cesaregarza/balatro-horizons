"""Native requirements stay visibly skipped until empirical evidence exists."""

import json
import shutil

import pytest

from balatro_horizons.config import ROOT, Environment
from balatro_horizons.evidence.certification import require_environment_certificate
from balatro_horizons.evidence.lock import lock_digest
from balatro_horizons.evidence.provenance import (
    accepted_source_matches,
    native_implementation_fingerprint,
)
from balatro_horizons.game.session import NativeFailure
from balatro_horizons.storage.journal import atomic_json, digest


def test_game_source_invalidates_native_fingerprint(tmp_path, monkeypatch):
    from balatro_horizons.evidence import provenance

    monkeypatch.setattr(provenance, "ROOT", tmp_path)
    source = tmp_path / "src/balatro_horizons/game/environment.py"
    source.parent.mkdir(parents=True)
    source.write_text("original game boundary")
    before = native_implementation_fingerprint()
    source.write_text("changed game boundary")
    assert native_implementation_fingerprint() != before


def test_harness_money_invalidates_full_fingerprint_not_native_identity(tmp_path, monkeypatch):
    from balatro_horizons.evidence import provenance

    monkeypatch.setattr(provenance, "ROOT", tmp_path)
    source = tmp_path / "src/balatro_horizons/harness/money.py"
    source.parent.mkdir(parents=True)
    source.write_text("original money")
    before_full = provenance.implementation_fingerprint()
    before_native = native_implementation_fingerprint()
    source.write_text("changed money")
    assert provenance.implementation_fingerprint() != before_full
    assert native_implementation_fingerprint() == before_native


def test_AT24_missing_native_certificate_blocks_evaluation():
    with pytest.raises(NativeFailure):
        require_environment_certificate({"unrecognized": "environment"}, Environment())


LOCK = {"fixture": "native-shaped-synthetic-only"}
MISMATCH = "^NATIVE_CAPABILITY_CERTIFICATE_MISMATCH$"


@pytest.fixture
def published(tmp_path, monkeypatch):
    """A passed certificate shaped like the one `publish` writes, over a source copy."""
    from balatro_horizons.evidence import certification, provenance

    shutil.copytree(ROOT / "src/balatro_horizons", tmp_path / "src/balatro_horizons",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(provenance, "ROOT", tmp_path)
    monkeypatch.setattr(certification, "ROOT", tmp_path)
    source = provenance.implementation_fingerprint()
    certificate = {
        "status": "passed", "environment_hash": digest(LOCK),
        "implementation_hash": source, "accepted_implementation_hash": source,
        "native_implementation_hash": native_implementation_fingerprint(),
        "configurations": [["RED", "WHITE"], ["RED", "GOLD"]],
    }
    path = tmp_path / "private/capability-certificate.json"
    atomic_json(path, certificate)
    assert require_environment_certificate(LOCK, Environment()) == certificate
    return tmp_path, path, certificate


def edit_source(root, path):
    file = root / "src/balatro_horizons" / path
    file.write_bytes(file.read_bytes() + b"\nEDITED_AFTER_CERTIFICATION = True\n")


@pytest.mark.parametrize("path", ["harness/context/render.py", "service.py"])
def test_harness_change_still_admits_runs(published, path):
    root, _, certificate = published
    edit_source(root, path)
    assert require_environment_certificate(LOCK, Environment()) == certificate


@pytest.mark.parametrize(
    "path", ["game/session.py", "observations/projection.py", "evidence/continuation_probe.py"]
)
def test_native_component_change_refuses_runs(published, path):
    root, _, _ = published
    edit_source(root, path)
    with pytest.raises(NativeFailure, match=MISMATCH):
        require_environment_certificate(LOCK, Environment())


def test_changed_lock_refuses_runs(published):
    with pytest.raises(NativeFailure, match=MISMATCH):
        require_environment_certificate({**LOCK, "bridge_sha256": "changed"}, Environment())


def test_uncertified_deck_and_stake_refuse_runs(published):
    with pytest.raises(NativeFailure, match=MISMATCH):
        require_environment_certificate(LOCK, Environment(deck="BLUE", stake="WHITE"))


def test_unpassed_certificate_refuses_runs(published):
    _, path, certificate = published
    atomic_json(path, {**certificate, "status": "failed"})
    with pytest.raises(NativeFailure, match=MISMATCH):
        require_environment_certificate(LOCK, Environment())


def test_certificate_without_native_hash_still_binds_the_whole_harness(published):
    root, path, certificate = published
    legacy = {key: value for key, value in certificate.items()
              if key not in ("native_implementation_hash", "accepted_implementation_hash")}
    atomic_json(path, legacy)
    assert require_environment_certificate(LOCK, Environment()) == legacy
    edit_source(root, "harness/context/render.py")
    with pytest.raises(NativeFailure, match=MISMATCH):
        require_environment_certificate(LOCK, Environment())

@pytest.mark.parametrize(
    "acceptance", ["AT-04", "AT-05", "AT-08", "AT-09", "AT-10", "AT-11", "AT-12", "AT-23"]
)
def test_native_evidence_gate(acceptance):
    path = ROOT / "reports/verification/native-evidence.json"
    if not path.is_file():
        pytest.skip(f"{acceptance}: missing artifact {path.relative_to(ROOT)}")
    report = json.loads(path.read_text())
    if not accepted_source_matches(report):
        pytest.skip(f"{acceptance}: {path.relative_to(ROOT)} does not accept current source")
    try:
        current_environment = lock_digest(ROOT)
    except ValueError as error:
        pytest.skip(f"{acceptance}: private/environment.lock.json: {error}")
    if report.get("environment_hash") != current_environment:
        pytest.skip(f"{acceptance}: private/environment.lock.json changed")
    item = report.get(acceptance)
    if not item or item["status"] != "passed":
        pytest.skip(
            f"{acceptance}: {path.relative_to(ROOT)}: "
            f"{item.get('reason', 'not verified') if item else 'entry missing'}"
        )
    assert item["evidence_kind"] == "NATIVE" and item["artifacts"]
    for artifact in item["artifacts"]:
        if not (ROOT / artifact).is_file():
            pytest.fail(f"{acceptance}: passed evidence names missing artifact {artifact}")
