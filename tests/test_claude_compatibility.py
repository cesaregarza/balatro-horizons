"""The Claude release preserves old OpenAI/baseline inputs, not old Claude behavior."""

from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.identity_migrations import (
    CLAUDE_SOURCE,
    CLAUDE_UPGRADES,
    WORKSPACE_UPGRADES,
)
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    native_component_manifest,
    source_files,
)
from balatro_horizons.evidence.reuse import revision_sources
from balatro_horizons.storage.journal import digest

DEPLOYED = "d30b772878457b57290dd4b88094d13a5d5ef196"
PREFIX = "src/balatro_horizons/"


@pytest.fixture
def deployed(monkeypatch):
    _, sources = revision_sources(ROOT, DEPLOYED)
    assert fingerprint_sources(sources) == CLAUDE_SOURCE
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: DEPLOYED)
    return sources


@pytest.mark.parametrize("provider", ["openai", "baseline"])
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_exact_deployed_delta_is_admitted_only_for_unchanged_provider_paths(deployed, provider, game_kind):
    current = source_files(ROOT)
    before, after = execution_manifest(deployed), execution_manifest(current)
    changed = {name.removeprefix(PREFIX): (before.get(name), after.get(name))
               for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    expected = dict(CLAUDE_UPGRADES)
    for path, (_, target) in WORKSPACE_UPGRADES.items():
        expected[path] = (expected[path][0], target)
    assert changed == expected
    assert native_component_manifest(deployed) == native_component_manifest(current)
    for path in ("harness/transport/openai.py", "harness/transport/base.py", "harness/money.py",
                 "harness/context/freeze.py", "harness/decision.py", "harness/runtime.py"):
        assert deployed[PREFIX + path] == current[PREFIX + path]
    assert execution_manifest(deployed, historical=True, preserved_provider=provider) == after
    protocol = {"implementation_hash": CLAUDE_SOURCE,
                "model": {"provider": "openai", "model": "gpt-6-luna"} if provider == "openai" else None}
    original = deepcopy(protocol)
    receipt = compatibility.prepare_compatibility({CLAUDE_SOURCE}, protocol, game_kind=game_kind)
    assert protocol == original
    assert receipt["preserved_provider"] == provider
    assert receipt["protocol_hash"] == digest(original)
    compatibility.validate_compatibility(receipt)
    receipt["preserved_provider"] = None
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.validate_compatibility(receipt)


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-legacy"])
def test_historical_claude_cannot_silently_adopt_new_provider_behavior(deployed, model):
    protocol = {"implementation_hash": CLAUDE_SOURCE, "model": {"provider": "anthropic", "model": model}}
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({CLAUDE_SOURCE}, protocol, game_kind="native")


@pytest.mark.parametrize("path", list(CLAUDE_UPGRADES))
def test_unreviewed_current_module_cannot_inherit_provider_scoped_approval(deployed, monkeypatch, path):
    current = source_files(ROOT)
    current[PREFIX + path] += b"\nUNREVIEWED_EXECUTION = True\n"
    monkeypatch.setattr(compatibility, "source_files", lambda _: current)
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility(
            {CLAUDE_SOURCE}, {"implementation_hash": CLAUDE_SOURCE, "model": None}, game_kind="native",
        )


def test_provider_scope_cannot_be_changed_independently_of_frozen_protocol(deployed, monkeypatch):
    protocol = {"implementation_hash": CLAUDE_SOURCE, "model": None}
    receipt = compatibility.prepare_compatibility({CLAUDE_SOURCE}, protocol, game_kind="native")
    receipt["preserved_provider"] = "openai"
    # Both providers have identical native identities; protocol binding must still refuse.
    compatibility.validate_compatibility(receipt)
    monkeypatch.setattr(compatibility, "read_compatibility", lambda *_: receipt)
    with pytest.raises(ValueError, match="^AGENT_PROTOCOL_IMPLEMENTATION_CHANGED$"):
        compatibility.require_protocol_compatibility(None, {}, protocol)


def test_unlisted_historical_fingerprint_cannot_use_the_new_module(deployed, monkeypatch):
    changed = {**deployed, PREFIX + "config.py": deployed[PREFIX + "config.py"] + b"\n# unlisted\n"}
    source = fingerprint_sources(changed)
    monkeypatch.setattr(compatibility, "revision_sources", lambda *_: (DEPLOYED, changed))
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({source}, {"implementation_hash": source}, game_kind="native")
