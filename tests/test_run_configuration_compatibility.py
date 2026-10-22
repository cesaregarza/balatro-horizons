"""Exact-source admission and frozen-input inheritance; no historical code execution."""

import json
from copy import deepcopy

import pytest
import test_campaign_budget
from test_budget_continuation import stopped
from test_restore_unfinished import finish, interrupted, request_for

from balatro_horizons.config import ROOT, Config
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.identity_migrations import (
    CLAUDE_UPGRADES,
    RUN_CONFIGURATION_SOURCE,
    RUN_CONFIGURATION_UPGRADES,
    WORKSPACE_UPGRADES,
)
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    native_component_manifest,
    source_files,
)
from balatro_horizons.evidence.reuse import revision_sources
from balatro_horizons.harness import decision, runtime
from balatro_horizons.harness.context import freeze
from balatro_horizons.harness.context.render import rules_kernel
from balatro_horizons.service_restore import restore_preview, start_restore
from balatro_horizons.storage.journal import digest
from balatro_horizons.workbench.budget_continuation import budget_preview

harness = test_campaign_budget.harness
DEPLOYED = "9dcd8bf5878338e090dc3363f5dd9d8d7bbd3893"
APPROVED_TARGET = "27d96f060be84dc77e1ca2d3900fb5887106463f"
PREFIX = "src/balatro_horizons/"


@pytest.fixture
def deployed_sources(monkeypatch):
    commit, sources = revision_sources(ROOT, DEPLOYED)
    assert commit == DEPLOYED and fingerprint_sources(sources) == RUN_CONFIGURATION_SOURCE
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: DEPLOYED)
    return sources


@pytest.fixture
def approved_target(monkeypatch):
    commit, sources = revision_sources(ROOT, APPROVED_TARGET)
    assert commit == APPROVED_TARGET
    # Source comparison only: no historical executable code is imported.
    monkeypatch.setattr(compatibility, "source_files", lambda _: sources)
    return sources


@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_deployed_source_has_only_the_exact_reviewed_execution_delta(
    deployed_sources, approved_target, game_kind,
):
    current = approved_target
    before, after = execution_manifest(deployed_sources), execution_manifest(current)
    changed = {name.removeprefix(PREFIX): (before.get(name), after.get(name))
               for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    expected = {**RUN_CONFIGURATION_UPGRADES, **CLAUDE_UPGRADES}
    for path, (_, target) in WORKSPACE_UPGRADES.items():
        expected[path] = (expected[path][0], target)
    assert changed == expected
    assert native_component_manifest(deployed_sources) == native_component_manifest(current)
    assert execution_manifest(deployed_sources, historical=True, preserved_provider="baseline") == after
    protocol = {"implementation_hash": RUN_CONFIGURATION_SOURCE}
    receipt = compatibility.prepare_compatibility(
        {RUN_CONFIGURATION_SOURCE}, protocol, game_kind=game_kind,
    )
    assert receipt["protocol_hash"] == digest(protocol)
    assert receipt["source_revisions"] == {RUN_CONFIGURATION_SOURCE: DEPLOYED}
    compatibility.validate_compatibility(receipt)


@pytest.mark.parametrize("path", [*RUN_CONFIGURATION_UPGRADES, *CLAUDE_UPGRADES])
@pytest.mark.parametrize("mutation", ["revert", "extra_statement"])
def test_current_module_mutations_cannot_inherit_approval(
    deployed_sources, approved_target, monkeypatch, path, mutation,
):
    current = dict(approved_target)
    monkeypatch.setattr(compatibility, "source_files", lambda _: current)
    protocol = {"implementation_hash": RUN_CONFIGURATION_SOURCE}
    compatibility.prepare_compatibility({RUN_CONFIGURATION_SOURCE}, protocol, game_kind="native")
    name = PREFIX + path
    if mutation == "revert":
        if name in deployed_sources:
            current[name] = deployed_sources[name]
        else:
            current.pop(name)
    else:
        current[name] += b"\nUNREVIEWED_CHANGE = True\n"
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({RUN_CONFIGURATION_SOURCE}, protocol, game_kind="native")


def test_unlisted_historical_source_cannot_use_the_pair_migration(
    deployed_sources, approved_target, monkeypatch,
):
    changed = dict(deployed_sources)
    # Even a comment-only change makes this a different full-source approval.
    changed[PREFIX + "harness/context/freeze.py"] += b"\n# unlisted release\n"
    old_hash = fingerprint_sources(changed)
    assert old_hash != RUN_CONFIGURATION_SOURCE
    monkeypatch.setattr(compatibility, "revision_sources", lambda *_: (DEPLOYED, changed))
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility(
            {old_hash}, {"implementation_hash": old_hash}, game_kind="native",
        )


@pytest.mark.parametrize("provider", ["baseline", "openai"])
@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_new_runtime_refuses_pair_approval_without_native_changes(deployed_sources, provider, game_kind):
    current = source_files(ROOT)
    assert native_component_manifest(deployed_sources) == native_component_manifest(current)
    assert execution_manifest(deployed_sources, historical=True, preserved_provider=provider) != execution_manifest(current)
    protocol = {"implementation_hash": RUN_CONFIGURATION_SOURCE,
                "model": None if provider == "baseline" else {"provider": provider}}
    original = deepcopy(protocol)
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        compatibility.prepare_compatibility({RUN_CONFIGURATION_SOURCE}, protocol, game_kind=game_kind)
    assert protocol == original


def legacy_freeze(config, policy, rules, **kwargs):
    # Construct legacy snapshot data; historical executable code is never loaded.
    bundle = freeze.freeze_protocol(config, policy, rules, **kwargs)
    bundle.pop("run_configuration")
    bundle["rules_kernel"] = rules_kernel(rules.get("skills", []))
    return bundle


def create_parent(h, monkeypatch, *, legacy, budget=False):
    h.config.environment.deck, h.config.environment.stake = "PLASMA", "ORANGE"
    with monkeypatch.context() as old:
        if legacy:
            old.setattr(runtime, "freeze_protocol", legacy_freeze)
            for module in (freeze, runtime, decision):
                old.setattr(module, "implementation_fingerprint", lambda: RUN_CONFIGURATION_SOURCE)
        return stopped(h)[0] if budget else interrupted(h)


def test_current_restore_and_branch_keep_original_inputs_and_parent_bytes(harness, monkeypatch):
    h = harness
    parent = create_parent(h, monkeypatch, legacy=False)
    original = {path: path.read_bytes() for path in h.store.episode_path(parent, True).rglob("*")
                if path.is_file() and path.name != "spending.json"}
    journal = h.store.episode_path(parent) / "events.jsonl"
    original[journal] = journal.read_bytes()
    protocol_path = h.store.episode_path(parent, True) / "agent-protocol.json"
    frozen = json.loads(original[protocol_path])
    assert "run_configuration" in frozen
    initial_prefix = h.calls[0]["input"][0]
    calls, games = len(h.calls), len(h.games)
    h.config.environment.deck, h.config.environment.stake = "RED", "WHITE"
    plan = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    assert plan["source_compatibility"] == "same_source"
    assert len(h.calls) == calls and len(h.games) == games and len(h.store.list_episodes()) == 1
    service = h.service()
    child = start_restore(service, parent, request_for(plan), paid_enabled=True)
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    saved = Config.model_validate(h.store.manifest(child, True)["config"])
    assert (saved.environment.deck, saved.environment.stake) == ("PLASMA", "ORANGE")
    branch = service.branch(saved, child, 0, "agent_continue")
    finish(service)
    assert h.store.summary(branch)["outcome"] == "WIN"
    for eid in (child, branch):
        assert (h.store.episode_path(eid, True) / "agent-protocol.json").read_bytes() == original[protocol_path]
        checkpoint = read_checkpoint(h.store, eid, 0)
        assert freeze.restore_protocol(h.store, checkpoint) == frozen
        assert checkpoint.get("source_compatibility") is None
    assert all(call["input"][0] == initial_prefix for call in h.calls)
    assert "Run configuration:" in initial_prefix["content"][0]["text"]
    assert {path: path.read_bytes() for path in original} == original
    h.native.assert_not_called()


def test_current_budget_child_preserves_pair(harness, monkeypatch):
    h = harness
    parent = create_parent(h, monkeypatch, legacy=False, budget=True)
    path = h.store.episode_path(parent, True) / "agent-protocol.json"
    original = path.read_bytes()
    before = json.loads(original)
    initial_prefix = h.calls[0]["input"][0]
    service = h.service()
    plan = budget_preview(service, parent, paid_enabled=True)["plan"]
    assert plan["source_compatibility"] == "same_source"
    child = service.continue_budget(
        parent, None, additional_cost=10, expected_head=plan["parent_terminal_hash"],
        expected_plan_hash=plan["plan_hash"], accept_compatible_update=True,
    )
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    assert path.read_bytes() == original
    after = json.loads((h.store.episode_path(child, True) / "agent-protocol.json").read_text())
    assert "run_configuration" in after
    assert after.get("run_configuration") == before.get("run_configuration")
    assert after["rules_kernel"] == before["rules_kernel"]
    assert all(call["input"][0] == initial_prefix for call in h.calls)
    h.native.assert_not_called()


def original_episode_bytes(store, parent):
    return {path: path.read_bytes()
            for private in (False, True)
            for path in store.episode_path(parent, private).rglob("*") if path.is_file()}


def assert_no_continuation(h, parent, original, calls, games, clients):
    assert (len(h.calls), len(h.games), len(h.clients)) == (calls, games, clients)
    assert len(h.store.list_episodes()) == 1
    assert {path: path.read_bytes() for path in original} == original
    added = original_episode_bytes(h.store, parent).keys() - original.keys()
    assert added <= {
        h.store.episode_path(parent, True) / "restore-admission.lock",
        h.store.episode_path(parent, True) / "budget-admission.lock",
    }
    assert all(path.read_bytes() == b"" for path in added)
    h.native.assert_not_called()


def test_new_runtime_refuses_legacy_restore_and_branch_without_side_effects(
    harness, deployed_sources, monkeypatch,
):
    h = harness
    parent = create_parent(h, monkeypatch, legacy=True)
    original = original_episode_bytes(h.store, parent)
    protocol_path = h.store.episode_path(parent, True) / "agent-protocol.json"
    assert "run_configuration" not in json.loads(original[protocol_path])
    assert "Run configuration:" not in h.calls[0]["input"][0]["content"][0]["text"]
    calls, games, clients = len(h.calls), len(h.games), len(h.clients)
    with pytest.raises(ValueError, match="^AGENT_PROTOCOL_IMPLEMENTATION_CHANGED$"):
        freeze.restore_protocol(h.store, read_checkpoint(h.store, parent, 0))
    with pytest.raises(ValueError, match="^CHECKPOINT_IMPLEMENTATION_CHANGED$"):
        h.service().branch(h.config, parent, 0, "agent_continue")
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    assert preview == {"episode_id": parent, "available": False,
                       "reason": "RESTORE_SOURCE_INCOMPATIBLE", "plan": None}
    request = request_for({"parent_head": h.store.summary(parent)["journal_head"],
                           "plan_hash": "0" * 64})
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        start_restore(h.service(), parent, request, paid_enabled=True)
    assert_no_continuation(h, parent, original, calls, games, clients)


def test_new_runtime_refuses_legacy_budget_child_without_side_effects(
    harness, deployed_sources, monkeypatch,
):
    h = harness
    parent = create_parent(h, monkeypatch, legacy=True, budget=True)
    original = original_episode_bytes(h.store, parent)
    protocol_path = h.store.episode_path(parent, True) / "agent-protocol.json"
    assert "run_configuration" not in json.loads(original[protocol_path])
    assert "Run configuration:" not in h.calls[0]["input"][0]["content"][0]["text"]
    calls, games, clients = len(h.calls), len(h.games), len(h.clients)
    service = h.service()
    preview = budget_preview(service, parent, paid_enabled=True)
    assert preview == {"episode_id": parent, "available": False,
                       "reason": "RESTORE_SOURCE_INCOMPATIBLE", "plan": None}
    with pytest.raises(ValueError, match="^RESTORE_SOURCE_INCOMPATIBLE$"):
        service.continue_budget(
            parent, 10, expected_head=h.store.summary(parent)["journal_head"],
            accept_compatible_update=True,
        )
    assert_no_continuation(h, parent, original, calls, games, clients)
