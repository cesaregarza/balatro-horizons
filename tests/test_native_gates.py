"""Native requirements stay visibly skipped until empirical evidence exists."""

import json

import pytest

from balatro_horizons.config import ROOT, Environment
from balatro_horizons.engine.certification import require_environment_certificate
from balatro_horizons.engine.native import NativeFailure
from balatro_horizons.engine.provenance import implementation_fingerprint
from balatro_horizons.storage.journal import digest


def test_AT24_missing_native_certificate_blocks_evaluation():
    with pytest.raises(NativeFailure):
        require_environment_certificate({"unrecognized": "environment"}, Environment())


@pytest.mark.parametrize(
    "acceptance", ["AT-04", "AT-05", "AT-08", "AT-09", "AT-10", "AT-11", "AT-12", "AT-23"]
)
def test_native_evidence_gate(acceptance):
    path = ROOT / "reports/verification/native-evidence.json"
    if not path.is_file():
        pytest.skip(f"{acceptance}: native evidence has not been collected")
    report = json.loads(path.read_text())
    if report.get("implementation_hash") != implementation_fingerprint():
        pytest.skip("Native evidence must be regenerated for changed source")
    lock = ROOT / "private/environment.lock.json"
    if not lock.is_file() or report.get("environment_hash") != digest(json.loads(lock.read_text())):
        pytest.skip("Native evidence must be regenerated for changed environment")
    item = report.get(acceptance)
    if not item or item["status"] != "passed":
        pytest.skip(
            f"{acceptance}: {item.get('reason', 'not verified') if item else 'not verified'}"
        )
    assert item["evidence_kind"] == "NATIVE" and item["artifacts"]
    for artifact in item["artifacts"]:
        assert (ROOT / artifact).is_file()
