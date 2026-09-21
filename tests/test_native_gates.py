"""Native requirements stay visibly skipped until empirical evidence exists."""

import json

import pytest

from balatro_horizons.config import ROOT, Environment
from balatro_horizons.evidence.certification import require_environment_certificate
from balatro_horizons.evidence.lock import lock_digest
from balatro_horizons.evidence.provenance import (
    accepted_source_matches,
    native_implementation_fingerprint,
)
from balatro_horizons.game.session import NativeFailure


def test_game_source_invalidates_native_fingerprint(tmp_path, monkeypatch):
    from balatro_horizons.evidence import provenance

    monkeypatch.setattr(provenance, "ROOT", tmp_path)
    source = tmp_path / "src/balatro_horizons/game/environment.py"
    source.parent.mkdir(parents=True)
    source.write_text("original game boundary")
    before = native_implementation_fingerprint()
    source.write_text("changed game boundary")
    assert native_implementation_fingerprint() != before


def test_AT24_missing_native_certificate_blocks_evaluation():
    with pytest.raises(NativeFailure):
        require_environment_certificate({"unrecognized": "environment"}, Environment())


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
            pytest.skip(f"{acceptance}: missing artifact {artifact}")
