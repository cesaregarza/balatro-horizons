"""Cross-source fixtures and guard mutations; no historical code is executed."""

import json
from copy import deepcopy

import pytest
import test_campaign_budget
from test_restore_unfinished import finish, interrupted, request_for

from balatro_horizons.config import ROOT
from balatro_horizons.evidence import compatibility
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.evidence.execution_identity import execution_manifest
from balatro_horizons.evidence.provenance import (
    fingerprint_sources,
    implementation_fingerprint,
    native_implementation_fingerprint,
    source_files,
)
from balatro_horizons.harness import decision, runtime
from balatro_horizons.harness.context import freeze
from balatro_horizons.service_restore import restore_preview, start_restore
from balatro_horizons.storage.journal import atomic_json, digest

harness = test_campaign_budget.harness


@pytest.fixture
def historical(harness, monkeypatch):
    h = harness
    sources = source_files(ROOT)
    # A different full identity with identical native and agent execution code.
    sources["src/balatro_horizons/service.py"] += b"\n# synthetic historical release\n"
    old_hash = fingerprint_sources(sources)
    assert old_hash != implementation_fingerprint()
    with monkeypatch.context() as old:
        for module in (freeze, runtime, decision):
            old.setattr(module, "implementation_fingerprint", lambda: old_hash)
        parent = interrupted(h)
    monkeypatch.setattr(compatibility, "certified_revision", lambda root, expected: "1" * 40)
    monkeypatch.setattr(compatibility, "revision_sources", lambda root, revision: (revision, sources))
    return h, parent, old_hash, sources


def test_older_source_needs_explicit_receipt_and_preserves_protocol_bytes(historical):
    h, parent, old_hash, _ = historical
    path = h.store.episode_path(parent, True) / "agent-protocol.json"
    original = path.read_bytes()
    checkpoint = read_checkpoint(h.store, parent, 0)
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_IMPLEMENTATION_CHANGED"):
        freeze.restore_protocol(h.store, checkpoint)
    plan = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    assert plan["source_compatibility"] == "compatible_update"
    with pytest.raises(ValueError, match="RESTORE_COMPATIBILITY_NOT_ACCEPTED"):
        start_restore(h.service(), parent, request_for(plan, accept_compatible_update=False), paid_enabled=True)
    assert len(h.store.list_episodes()) == 1
    service = h.service()
    child = start_restore(service, parent, request_for(plan), paid_enabled=True)
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    assert path.read_bytes() == original
    assert read_checkpoint(h.store, parent, 0) == checkpoint
    child_protocol = json.loads((h.store.episode_path(child, True) / "agent-protocol.json").read_text())
    assert child_protocol == json.loads(original)
    assert child_protocol["implementation_hash"] == old_hash
    resume = read_checkpoint(h.store, child, 0)
    assert resume["implementation_hash"] == implementation_fingerprint()
    assert freeze.restore_protocol(h.store, resume) == child_protocol
    proof_path = h.store.episode_path(child, True) / "source-compatibility.json"
    proof = json.loads(proof_path.read_text())
    assert proof["source_revisions"] == {old_hash: "1" * 40}
    assert proof["protocol_hash"] == digest(child_protocol)
    proof["accepted_implementation_hash"] = "f" * 64
    atomic_json(proof_path, proof)
    with pytest.raises(ValueError, match="RESTORE_COMPATIBILITY_INVALID"):
        freeze.restore_protocol(h.store, resume)


@pytest.mark.parametrize("target", [
    "game/replay.py", "harness/decision.py", "harness/context/render.py",
    "harness/money.py", "config.py", "service.py", "service_execution.py",
])
def test_changed_native_or_agent_execution_refuses_migration(historical, target):
    h, parent, _, sources = historical
    sources["src/balatro_horizons/" + target] += b"\nMUTATED_EXECUTABLE_CONTRACT = True\n"
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    assert not preview["available"] and preview["reason"] == "RESTORE_SOURCE_INCOMPATIBLE"
    assert len(h.games) == 1 and len(h.store.list_episodes()) == 1


def test_compatibility_revalidates_current_source_and_bound_protocol(historical):
    h, parent, old_hash, _ = historical
    bundle = freeze.read_protocol(h.store, read_checkpoint(h.store, parent, 0))
    proof = compatibility.prepare_compatibility({old_hash}, bundle)
    changed = deepcopy(proof)
    changed["accepted_implementation_hash"] = "f" * 64
    with pytest.raises(ValueError, match="RESTORE_COMPATIBILITY_STALE"):
        compatibility.validate_compatibility(changed)
    changed = deepcopy(proof)
    changed["source_revisions"] = {"a" * 64: "1" * 40}
    with pytest.raises(ValueError, match="RESTORE_SOURCE_INCOMPATIBLE"):
        compatibility.validate_compatibility(changed)


def test_source_normalization_does_not_hide_removed_or_changed_guards():
    prefix = "src/balatro_horizons/harness/"
    for suffix, source in (("decision.py", '''
def _check_protocol_integrity(self):
    if self.protocol and self.protocol["implementation_hash"] != implementation_fingerprint():
        raise HarnessFailure("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED", stage="decision_start")
'''), ("context/freeze.py", '''
def restore_protocol(store, checkpoint):
    if bundle["implementation_hash"] != current_implementation:
        raise ValueError("AGENT_PROTOCOL_IMPLEMENTATION_CHANGED")
''')):
        baseline = execution_manifest({prefix + suffix: source.encode()})
        for altered in (source.replace(" != ", " == "), source.replace("raise ", "return ")):
            assert execution_manifest({prefix + suffix: altered.encode()}) != baseline


def test_missing_historical_source_is_a_safe_preview_refusal(historical, monkeypatch):
    h, parent, _, _ = historical
    def unavailable(*args):
        raise ValueError("CERTIFIED_BASELINE_REVISION_NOT_FOUND")
    monkeypatch.setattr(compatibility, "certified_revision", unavailable)
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    assert preview["reason"] == "RESTORE_SOURCE_HISTORY_UNAVAILABLE"
    assert len(h.store.list_episodes()) == 1


def test_source_change_during_protocol_freeze_is_not_silently_rebound(harness, monkeypatch):
    original = runtime.freeze_protocol
    def changed_after_freeze(*args, **kwargs):
        protocol = original(*args, **kwargs)
        monkeypatch.setattr(runtime, "implementation_fingerprint", lambda: "f" * 64)
        monkeypatch.setattr(decision, "implementation_fingerprint", lambda: "f" * 64)
        return protocol
    monkeypatch.setattr(runtime, "freeze_protocol", changed_after_freeze)
    result = harness.service().execute(harness.config, "luna", "PRIVATE_SOURCE_FIXTURE", offline=True)
    assert result["reason"] == "AGENT_PROTOCOL_IMPLEMENTATION_CHANGED"
    assert not harness.calls


def test_v1_matches_the_supported_pre_recovery_source_contract():
    # Source-only identities independently measured from committed release
    # 5ad42191e760388e4ebb953bb13c2ebb84a48dae; no run/private data is embedded.
    # A behavior change requires an explicit compatibility-policy decision.
    sources = source_files(ROOT)
    assert native_implementation_fingerprint(sources) == "a5662e2530340108571af4e1238f7a691b75dd0b75f9cb2deafd496e11ced3c0"
    assert digest(execution_manifest(sources)) == "07b4becdd1718055055b141f49cd56b3b00d277f0f666399ee07a5bdbb524ccb"


@pytest.mark.parametrize("path", [
    "service_restore.py", "workbench/restoration.py", "workbench/restore_ledger.py",
    "evidence/compatibility.py", "evidence/execution_identity.py",
])
def test_restore_policy_and_compatibility_changes_invalidate_source_identity(path):
    sources = source_files(ROOT)
    before = fingerprint_sources(sources)
    sources["src/balatro_horizons/" + path] += b"\n# source identity mutation\n"
    assert fingerprint_sources(sources) != before
