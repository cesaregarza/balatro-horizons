"""Runs recorded before the model tables restore only across that exact change."""

from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.identity_migrations import (
    MODEL_TABLE_SOURCES,
    MODEL_TABLE_UPGRADES,
    NATIVE_GATE_SOURCE,
    NATIVE_GATE_UPGRADES,
)
from balatro_horizons.evidence.provenance import fingerprint_sources, native_component_manifest
from balatro_horizons.evidence.reuse import revision_sources
from balatro_horizons.storage.journal import digest

CERTIFIED = "68fe9fa25844eb23836292ab89de0cb594d4a1f2"
MAIN = "6b95c0c18d262512829a9e0c6fb4e82bd46defcd"
APPROVED_TARGET = "640a198a4ca3ce210c03377bfe92b1a256d1ba07"
PREFIX = "src/balatro_horizons/"
PROTOCOLS = {
    "anthropic": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
    "openai": {"provider": "openai", "model": "gpt-6-luna"},
    "baseline": None,
}


@pytest.fixture(params=[CERTIFIED, MAIN])
def deployed(request, monkeypatch):
    _, sources = revision_sources(ROOT, request.param)
    assert fingerprint_sources(sources) in MODEL_TABLE_SOURCES
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: request.param)
    return request.param, sources


@pytest.fixture
def approved_target(monkeypatch):
    commit, sources = revision_sources(ROOT, APPROVED_TARGET)
    assert commit == APPROVED_TARGET
    # Evaluate the immutable reviewed target without executing historical code.
    monkeypatch.setattr(compatibility, "source_files", lambda _: sources)
    return sources


def changes(before, after):
    return {
        name.removeprefix(PREFIX): (before.get(name), after.get(name))
        for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    }


def test_both_deployed_sources_are_listed():
    sources = {
        fingerprint_sources(revision_sources(ROOT, commit)[1]) for commit in (CERTIFIED, MAIN)
    }
    assert sources == set(MODEL_TABLE_SOURCES)
    assert NATIVE_GATE_SOURCE in MODEL_TABLE_SOURCES


@pytest.mark.parametrize("provider", list(PROTOCOLS))
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_model_tables_are_the_only_execution_delta(deployed, approved_target, provider, game_kind):
    commit, sources = deployed
    source = fingerprint_sources(sources)
    before, after = execution_manifest(sources), execution_manifest(approved_target)
    expected = dict(MODEL_TABLE_UPGRADES)
    if source == NATIVE_GATE_SOURCE:
        expected.update(NATIVE_GATE_UPGRADES)
    assert changes(before, after) == expected
    assert native_component_manifest(sources) == native_component_manifest(approved_target)
    assert execution_manifest(sources, historical=True) == after
    protocol = {"implementation_hash": source, "model": PROTOCOLS[provider]}
    original = deepcopy(protocol)
    receipt = compatibility.prepare_compatibility({source}, protocol, game_kind=game_kind)
    assert protocol == original and receipt["protocol_hash"] == digest(original)
    assert receipt["source_revisions"] == {source: commit}
    compatibility.validate_compatibility(receipt)


@pytest.mark.parametrize("path", list(MODEL_TABLE_UPGRADES))
@pytest.mark.parametrize("mutation", ["revert", "extra_statement", "missing"])
def test_unreviewed_target_cannot_inherit_model_table_approval(
    deployed,
    approved_target,
    monkeypatch,
    path,
    mutation,
):
    _, sources = deployed
    source = fingerprint_sources(sources)
    current = dict(approved_target)
    protocol = {"implementation_hash": source, "model": PROTOCOLS["anthropic"]}
    compatibility.prepare_compatibility({source}, protocol, game_kind="native")
    name = PREFIX + path
    if mutation == "revert":
        if name in sources:
            current[name] = sources[name]
        else:
            current.pop(name)
    elif mutation == "missing":
        current.pop(name)
    else:
        current[name] += b"\nUNREVIEWED_MODEL_TABLE_CHANGE = True\n"
    monkeypatch.setattr(compatibility, "source_files", lambda _: current)
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({source}, protocol, game_kind="native")


def test_unknown_historical_fingerprint_cannot_use_model_table_mapping(
    deployed,
    approved_target,
    monkeypatch,
):
    commit, sources = deployed
    altered = {**sources, PREFIX + "config.py": sources[PREFIX + "config.py"] + b"\n# unlisted\n"}
    source = fingerprint_sources(altered)
    monkeypatch.setattr(compatibility, "revision_sources", lambda *_: (commit, altered))
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility(
            {source},
            {"implementation_hash": source},
            game_kind="native",
        )
