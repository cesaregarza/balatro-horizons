"""Repeat the gate's mutations in-process; never edit source or launch native games.

Run with: .venv/bin/python -m pytest -q tests/test_restore_mutations.py
Each deliberate bypass must make the corresponding ordinary regression assertion
fail. This pins the tests' ability to catch the bug, not just their baseline pass.
"""

import pytest
import test_campaign_budget
import test_restore_compatibility as compatibility_checks
import test_restore_identity as identity_checks
import test_restore_refusals as refusal_checks

from balatro_horizons.evidence import compatibility
from balatro_horizons.workbench import restoration, restore_ledger

harness = test_campaign_budget.harness
historical = compatibility_checks.historical
DECISION_CHANGE = (
    "harness/decision.py", b"!= implementation_fingerprint()", b"== implementation_fingerprint()",
)
DECISION_PATH = "src/balatro_horizons/harness/decision.py"


@pytest.mark.parametrize("historical", [DECISION_CHANGE], indirect=True)
@pytest.mark.parametrize("mutation", ["constant_identity", "omitted_decision"])
def test_behavioral_refusal_test_detects_identity_bypasses(historical, monkeypatch, mutation):
    if mutation == "constant_identity":
        monkeypatch.setattr(compatibility, "_identity", lambda *args, **kwargs: {
            "native_hash": "constant", "execution_hash": "constant",
        })
    else:
        original = compatibility.execution_manifest
        def omitted(*args, **kwargs):
            return {k: v for k, v in original(*args, **kwargs).items() if k != DECISION_PATH}
        monkeypatch.setattr(compatibility, "execution_manifest", omitted)
    with pytest.raises(AssertionError):
        compatibility_checks.test_changed_native_or_agent_execution_refuses_migration(historical)


def test_inventory_test_detects_removed_file_set(monkeypatch):
    original = identity_checks.execution_manifest
    def omitted(*args, **kwargs):
        return {k: v for k, v in original(*args, **kwargs).items()
                if "/harness/" not in k}
    monkeypatch.setattr(identity_checks, "execution_manifest", omitted)
    with pytest.raises(AssertionError):
        identity_checks.test_every_fingerprinted_file_has_an_explicit_comparison_or_exclusion()


def test_directionality_test_detects_symmetric_normalization(monkeypatch):
    original = identity_checks.execution_manifest
    monkeypatch.setattr(identity_checks, "execution_manifest",
                        lambda sources, **kwargs: original(sources, historical=True))
    with pytest.raises(AssertionError):
        identity_checks.test_calibration_migration_is_one_way_and_never_normalizes_current_regression()


@pytest.mark.parametrize("child", [False, True], ids=["lost_root", "lost_restore_child"])
def test_terminal_regressions_detect_the_wrong_loss_name(harness, monkeypatch, child):
    for module in (restoration, restore_ledger):
        monkeypatch.setattr(module, "GAME_TERMINAL_OUTCOMES", frozenset({"WIN", "LOSS"}))
    check = (refusal_checks.test_finished_restore_child_closes_the_entire_restore_family if child
             else refusal_checks.test_finished_root_cannot_restore)
    with pytest.raises(AssertionError):
        check(harness, monkeypatch, "GAME_LOSS")
