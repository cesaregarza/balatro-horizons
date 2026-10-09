"""Exact-source admission and frozen-input inheritance; no historical code execution."""

import json

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
PREFIX = "src/balatro_horizons/"


@pytest.fixture
def deployed_sources(monkeypatch):
    commit, sources = revision_sources(ROOT, DEPLOYED)
    assert commit == DEPLOYED and fingerprint_sources(sources) == RUN_CONFIGURATION_SOURCE
    monkeypatch.setattr(compatibility, "certified_revision", lambda *_: DEPLOYED)
    return sources


@pytest.mark.parametrize("game_kind", ["native", "synthetic"])
def test_deployed_source_has_only_the_exact_reviewed_execution_delta(deployed_sources, game_kind):
    current = source_files(ROOT)
    before, after = execution_manifest(deployed_sources), execution_manifest(current)
    changed = {name.removeprefix(PREFIX): (before.get(name), after.get(name))
               for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    assert changed == {**RUN_CONFIGURATION_UPGRADES, **CLAUDE_UPGRADES}
    assert native_component_manifest(deployed_sources) == native_component_manifest(current)
    assert execution_manifest(deployed_sources, historical=True, preserved_provider="baseline") == after
    receipt = compatibility.prepare_compatibility(
        {RUN_CONFIGURATION_SOURCE}, {"implementation_hash": RUN_CONFIGURATION_SOURCE},
        game_kind=game_kind,
    )
    assert receipt["source_revisions"] == {RUN_CONFIGURATION_SOURCE: DEPLOYED}
    compatibility.validate_compatibility(receipt)


@pytest.mark.parametrize("path", [*RUN_CONFIGURATION_UPGRADES, *CLAUDE_UPGRADES])
@pytest.mark.parametrize("mutation", ["revert", "extra_statement"])
def test_current_module_mutations_cannot_inherit_approval(deployed_sources, monkeypatch, path, mutation):
    current = source_files(ROOT)
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


def test_unlisted_historical_source_cannot_use_the_pair_migration(deployed_sources, monkeypatch):
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


@pytest.mark.parametrize("legacy", [False, True], ids=["new_pair", "deployed_legacy"])
def test_restore_and_branch_keep_original_inputs_and_parent_bytes(
    harness, deployed_sources, monkeypatch, legacy,
):
    h = harness
    parent = create_parent(h, monkeypatch, legacy=legacy)
    original = {path: path.read_bytes() for path in h.store.episode_path(parent, True).rglob("*")
                if path.is_file() and path.name != "spending.json"}
    journal = h.store.episode_path(parent) / "events.jsonl"
    original[journal] = journal.read_bytes()
    protocol_path = h.store.episode_path(parent, True) / "agent-protocol.json"
    frozen = json.loads(original[protocol_path])
    assert ("run_configuration" not in frozen) is legacy
    initial_prefix = h.calls[0]["input"][0]
    calls, games = len(h.calls), len(h.games)
    if legacy:
        with pytest.raises(ValueError, match="^AGENT_PROTOCOL_IMPLEMENTATION_CHANGED$"):
            freeze.restore_protocol(h.store, read_checkpoint(h.store, parent, 0))
        with pytest.raises(ValueError, match="^CHECKPOINT_IMPLEMENTATION_CHANGED$"):
            h.service().branch(h.config, parent, 0, "agent_continue")
    h.config.environment.deck, h.config.environment.stake = "RED", "WHITE"
    plan = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    assert plan["source_compatibility"] == ("compatible_update" if legacy else "same_source")
    if legacy:
        with pytest.raises(ValueError, match="^RESTORE_COMPATIBILITY_NOT_ACCEPTED$"):
            start_restore(h.service(), parent, request_for(plan, accept_compatible_update=False),
                          paid_enabled=True)
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
        assert (checkpoint.get("source_compatibility") is not None) is legacy
    assert all(call["input"][0] == initial_prefix for call in h.calls)
    assert ("Run configuration:" not in initial_prefix["content"][0]["text"]) is legacy
    assert {path: path.read_bytes() for path in original} == original
    h.native.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True], ids=["new_pair", "deployed_legacy"])
def test_budget_child_preserves_pair_or_legacy_absence(harness, deployed_sources, monkeypatch, legacy):
    h = harness
    parent = create_parent(h, monkeypatch, legacy=legacy, budget=True)
    path = h.store.episode_path(parent, True) / "agent-protocol.json"
    original = path.read_bytes()
    before = json.loads(original)
    initial_prefix = h.calls[0]["input"][0]
    service = h.service()
    plan = budget_preview(service, parent, paid_enabled=True)["plan"]
    assert plan["source_compatibility"] == ("compatible_update" if legacy else "same_source")
    child = service.continue_budget(
        parent, None, additional_cost=10, expected_head=plan["parent_terminal_hash"],
        expected_plan_hash=plan["plan_hash"], accept_compatible_update=True,
    )
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    assert path.read_bytes() == original
    after = json.loads((h.store.episode_path(child, True) / "agent-protocol.json").read_text())
    assert ("run_configuration" not in after) is legacy
    assert after.get("run_configuration") == before.get("run_configuration")
    assert after["rules_kernel"] == before["rules_kernel"]
    assert all(call["input"][0] == initial_prefix for call in h.calls)
    if legacy:
        proof = json.loads((h.store.episode_path(child, True) / "source-compatibility.json").read_text())
        assert proof["protocol_hash"] == digest(before)
        assert proof["budget_protocol_hash"] == digest(after)
    h.native.assert_not_called()
