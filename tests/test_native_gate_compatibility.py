"""Runs recorded before the native run gate restore only across that exact change."""

from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.identity_migrations import NATIVE_GATE_SOURCE, NATIVE_GATE_UPGRADES
from balatro_horizons.evidence.provenance import fingerprint_sources, native_component_manifest
from balatro_horizons.evidence.reuse import revision_sources
from balatro_horizons.storage.journal import digest

DEPLOYED = "68fe9fa25844eb23836292ab89de0cb594d4a1f2"
APPROVED_TARGET = "7d0165c5f2b8fb78652e8c199b93ee7e50c454bd"
PREFIX = "src/balatro_horizons/"


@pytest.fixture
def deployed(monkeypatch):
    _, sources = revision_sources(ROOT, DEPLOYED)
    assert fingerprint_sources(sources) == NATIVE_GATE_SOURCE
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: DEPLOYED)
    return sources


@pytest.fixture
def approved_target(monkeypatch):
    commit, sources = revision_sources(ROOT, APPROVED_TARGET)
    assert commit == APPROVED_TARGET
    # Evaluate the immutable reviewed target without executing historical code.
    monkeypatch.setattr(compatibility, "source_files", lambda _: sources)
    return sources


@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_native_gate_rule_is_the_only_execution_delta(deployed, approved_target, game_kind):
    before, after = execution_manifest(deployed), execution_manifest(approved_target)
    changed = {name.removeprefix(PREFIX): (before.get(name), after.get(name))
               for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    assert changed == NATIVE_GATE_UPGRADES
    assert native_component_manifest(deployed) == native_component_manifest(approved_target)
    assert execution_manifest(deployed, historical=True) == after
    protocol = {"implementation_hash": NATIVE_GATE_SOURCE}
    original = deepcopy(protocol)
    receipt = compatibility.prepare_compatibility({NATIVE_GATE_SOURCE}, protocol, game_kind=game_kind)
    assert protocol == original and receipt["protocol_hash"] == digest(original)
    assert receipt["source_revisions"] == {NATIVE_GATE_SOURCE: DEPLOYED}
    compatibility.validate_compatibility(receipt)


@pytest.mark.parametrize("mutation", ["revert", "extra_statement", "missing"])
def test_unreviewed_target_cannot_inherit_native_gate_approval(
    deployed, approved_target, monkeypatch, mutation,
):
    current = dict(approved_target)
    protocol = {"implementation_hash": NATIVE_GATE_SOURCE}
    compatibility.prepare_compatibility({NATIVE_GATE_SOURCE}, protocol, game_kind="native")
    name = PREFIX + "evidence/provenance.py"
    if mutation == "revert":
        current[name] = deployed[name]
    elif mutation == "missing":
        current.pop(name)
    else:
        current[name] += b"\nUNREVIEWED_GATE_CHANGE = True\n"
    monkeypatch.setattr(compatibility, "source_files", lambda _: current)
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({NATIVE_GATE_SOURCE}, protocol, game_kind="native")


def test_unknown_historical_fingerprint_cannot_use_native_gate_mapping(
    deployed, approved_target, monkeypatch,
):
    altered = {**deployed, PREFIX + "config.py": deployed[PREFIX + "config.py"] + b"\n# unlisted\n"}
    source = fingerprint_sources(altered)
    monkeypatch.setattr(compatibility, "revision_sources", lambda *_: (DEPLOYED, altered))
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility(
            {source}, {"implementation_hash": source}, game_kind="native",
        )
