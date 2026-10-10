"""Workspace approval is historical, not permission to adopt a new runtime."""

from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.identity_migrations import WORKSPACE_SOURCE, WORKSPACE_UPGRADES
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    native_component_manifest,
    source_files,
)
from balatro_horizons.evidence.reuse import revision_sources
from balatro_horizons.storage.journal import digest

DEPLOYED = "3f953d94c31f007a824599648b6a2fe9f712d523"
APPROVED_TARGET = "27d96f060be84dc77e1ca2d3900fb5887106463f"
PREFIX = "src/balatro_horizons/"


@pytest.fixture
def deployed(monkeypatch):
    _, sources = revision_sources(ROOT, DEPLOYED)
    assert fingerprint_sources(sources) == WORKSPACE_SOURCE
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: DEPLOYED)
    return sources


@pytest.fixture
def approved_target(monkeypatch):
    commit, sources = revision_sources(ROOT, APPROVED_TARGET)
    assert commit == APPROVED_TARGET
    # Evaluate the immutable reviewed target without executing historical code.
    monkeypatch.setattr(compatibility, "source_files", lambda _: sources)
    return sources


@pytest.mark.parametrize("provider", ["anthropic", "openai", "baseline"])
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_workspace_routing_keeps_deployed_protocol_and_native_execution(
    deployed, approved_target, provider, game_kind,
):
    current = approved_target
    before, after = execution_manifest(deployed), execution_manifest(current)
    changed = {name.removeprefix(PREFIX): (before.get(name), after.get(name))
               for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    assert changed == WORKSPACE_UPGRADES
    assert native_component_manifest(deployed) == native_component_manifest(current)
    protocol = {"implementation_hash": WORKSPACE_SOURCE,
                "model": None if provider == "baseline" else {"provider": provider}}
    original = deepcopy(protocol)
    receipt = compatibility.prepare_compatibility({WORKSPACE_SOURCE}, protocol, game_kind=game_kind)
    assert protocol == original and receipt["protocol_hash"] == digest(original)
    assert receipt["source_revisions"] == {WORKSPACE_SOURCE: DEPLOYED}
    compatibility.validate_compatibility(receipt)


@pytest.mark.parametrize("path", list(WORKSPACE_UPGRADES))
@pytest.mark.parametrize("mutation", ["revert", "extra_statement", "missing"])
def test_unreviewed_target_cannot_inherit_workspace_approval(
    deployed, approved_target, monkeypatch, path, mutation,
):
    current = dict(approved_target)
    protocol = {"implementation_hash": WORKSPACE_SOURCE, "model": {"provider": "anthropic"}}
    compatibility.prepare_compatibility({WORKSPACE_SOURCE}, protocol, game_kind="native")
    name = PREFIX + path
    if mutation == "revert":
        current[name] = deployed[name]
    elif mutation == "missing":
        current.pop(name)
    else:
        current[name] += b"\nUNREVIEWED_WORKSPACE_CHANGE = True\n"
    monkeypatch.setattr(compatibility, "source_files", lambda _: current)
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({WORKSPACE_SOURCE}, protocol, game_kind="native")


def test_unknown_historical_fingerprint_cannot_use_workspace_mapping(
    deployed, approved_target, monkeypatch,
):
    altered = {**deployed, PREFIX + "config.py": deployed[PREFIX + "config.py"] + b"\n# unlisted\n"}
    source = fingerprint_sources(altered)
    monkeypatch.setattr(compatibility, "revision_sources", lambda *_: (DEPLOYED, altered))
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility(
            {source}, {"implementation_hash": source}, game_kind="native",
        )


@pytest.mark.parametrize("provider", ["anthropic", "openai", "baseline"])
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_new_runtime_refuses_workspace_approval_without_native_changes(deployed, provider, game_kind):
    current = source_files(ROOT)
    assert native_component_manifest(deployed) == native_component_manifest(current)
    assert execution_manifest(deployed, historical=True, preserved_provider=provider) != execution_manifest(current)
    protocol = {"implementation_hash": WORKSPACE_SOURCE,
                "model": None if provider == "baseline" else {"provider": provider}}
    original = deepcopy(protocol)
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({WORKSPACE_SOURCE}, protocol, game_kind=game_kind)
    assert protocol == original
