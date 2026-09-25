"""Exact deployed-source upgrade; never execute historical code or native games."""

import json

import pytest
import test_campaign_budget
from test_budget_continuation import stopped
from test_restore_unfinished import finish

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.identity_migrations import FUNDING_SOURCE, FUNDING_UPGRADES
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    native_component_manifest,
    source_files,
)
from balatro_horizons.evidence.reuse import revision_sources
from balatro_horizons.harness import decision, runtime
from balatro_horizons.harness.context import freeze
from balatro_horizons.storage.journal import atomic_json, digest
from balatro_horizons.workbench.budget_continuation import budget_preview

harness = test_campaign_budget.harness
DEPLOYED = "8807caf56828351c8a0ca12b5a59b2c944a4b7aa"
PREFIX = "src/balatro_horizons/"


@pytest.mark.parametrize("path", list(FUNDING_UPGRADES))
def test_funding_catalogue_pins_current_and_original_asts(path):
    _, historical = revision_sources(ROOT, DEPLOYED)
    old, accepted = FUNDING_UPGRADES[path]
    assert execution_manifest(historical).get(PREFIX + path) == old
    assert execution_manifest(source_files(ROOT))[PREFIX + path] == accepted


@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_deployed_source_passes_only_explicit_one_way_upgrade(monkeypatch, game_kind):
    _, historical = revision_sources(ROOT, DEPLOYED)
    current = source_files(ROOT)
    assert fingerprint_sources(historical) == FUNDING_SOURCE
    assert native_component_manifest(historical) == native_component_manifest(current)
    assert execution_manifest(historical, historical=True) == execution_manifest(current)
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: DEPLOYED)
    receipt = compatibility.prepare_compatibility(
        {FUNDING_SOURCE}, {"implementation_hash": FUNDING_SOURCE}, game_kind=game_kind,
    )
    assert receipt["source_revisions"] == {FUNDING_SOURCE: DEPLOYED}
    compatibility.validate_compatibility(receipt)


def test_funding_catalogue_exactly_equals_the_raw_manifest_delta():
    _, historical = revision_sources(ROOT, DEPLOYED)
    previous = execution_manifest(historical)
    current = execution_manifest(source_files(ROOT))
    changed = {name.removeprefix(PREFIX): (previous.get(name), current.get(name))
               for name in previous.keys() | current.keys()
               if previous.get(name) != current.get(name)}
    assert changed == FUNDING_UPGRADES


@pytest.mark.parametrize("path", list(FUNDING_UPGRADES))
def test_current_execution_mutation_does_not_inherit_funding_approval(monkeypatch, path):
    changed = source_files(ROOT)
    changed[PREFIX + path] += b"\nUNREVIEWED_EXECUTABLE_CHANGE = True\n"
    monkeypatch.setattr(compatibility, "source_files", lambda _: changed)
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: DEPLOYED)
    with pytest.raises(ValueError, match="RESTORE_SOURCE_INCOMPATIBLE"):
        compatibility.prepare_compatibility(
            {FUNDING_SOURCE}, {"implementation_hash": FUNDING_SOURCE}, game_kind="native",
        )


def test_missing_new_policy_is_not_allowed_on_unlisted_historical_source():
    _, historical = revision_sources(ROOT, DEPLOYED)
    historical[PREFIX + "config.py"] += b"\n# an unlisted source identity\n"
    assert fingerprint_sources(historical) != FUNDING_SOURCE
    assert PREFIX + "cost_limits.py" not in execution_manifest(historical, historical=True)


def test_budget_child_from_deployed_source_requires_acceptance_and_keeps_protocol(harness, monkeypatch):
    h = harness
    # Emulate an older frozen protocol with mocked provider/game only.
    with monkeypatch.context() as old:
        for module in (freeze, runtime, decision):
            old.setattr(module, "implementation_fingerprint", lambda: FUNDING_SOURCE)
        parent, _, _, _ = stopped(h)
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: DEPLOYED)
    path = h.store.episode_path(parent, True) / "agent-protocol.json"
    original = path.read_bytes()
    service = h.service()
    response = budget_preview(service, parent, paid_enabled=True)
    assert response["available"], response
    plan = response["plan"]
    assert plan["source_compatibility"] == "compatible_update"
    arguments = {"expected_head": plan["parent_terminal_hash"], "additional_cost": 10,
                 "expected_plan_hash": plan["plan_hash"]}
    calls = len(h.calls)
    with pytest.raises(ValueError, match="RESTORE_COMPATIBILITY_NOT_ACCEPTED"):
        service.continue_budget(parent, None, **arguments)
    assert len(h.store.list_episodes()) == 1 and len(h.calls) == calls
    child = service.continue_budget(parent, None, accept_compatible_update=True, **arguments)
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    assert path.read_bytes() == original
    proof = json.loads((h.store.episode_path(child, True) / "source-compatibility.json").read_text())
    assert proof["source_revisions"] == {FUNDING_SOURCE: DEPLOYED}
    checkpoint = read_checkpoint(h.store, child, plan["decision"])
    restored = freeze.restore_protocol(h.store, checkpoint)
    assert restored["implementation_hash"] == FUNDING_SOURCE
    assert restored["episode_limits"]["max_episode_cost_usd"] == plan["new_cap_usd"]
    assert proof["budget_protocol_hash"] == digest(restored)
    # Even an internally rehashed child checkpoint cannot change the approved cap.
    restored["episode_limits"]["max_episode_cost_usd"] += 1
    atomic_json(h.store.episode_path(child, True) / "agent-protocol.json", restored)
    checkpoint["agent_protocol"]["hash"] = digest(restored)
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_IMPLEMENTATION_CHANGED"):
        freeze.restore_protocol(h.store, checkpoint)
    h.native.assert_not_called()
