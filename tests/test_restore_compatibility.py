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
    source_files,
)
from balatro_horizons.harness import decision, runtime
from balatro_horizons.harness.context import freeze
from balatro_horizons.service_restore import restore_preview, start_restore
from balatro_horizons.storage.journal import atomic_json, digest

harness = test_campaign_budget.harness


@pytest.fixture
def historical(harness, monkeypatch, request):
    h = harness
    sources = source_files(ROOT)
    # A different full identity with identical native and agent execution code.
    sources["src/balatro_horizons/service.py"] += b"\n# synthetic historical release\n"
    if getattr(request, "param", None):
        target, before, after = request.param
        path = "src/balatro_horizons/" + target
        assert before in sources[path]
        sources[path] = sources[path].replace(before, after, 1)
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
    assert h.store.manifest(child)["restoration"]["source_revisions"] == ["1" * 40]
    assert proof["game_kind"] == "synthetic"
    assert proof["protocol_hash"] == digest(child_protocol)
    proof["accepted_implementation_hash"] = "f" * 64
    atomic_json(proof_path, proof)
    with pytest.raises(ValueError, match="RESTORE_COMPATIBILITY_INVALID"):
        freeze.restore_protocol(h.store, resume)


@pytest.mark.parametrize("historical", [
    ("game/replay.py", b"import ", b"# changed native bytes\nimport "),
    ("game/fake.py", b"self.money += 4", b"self.money += 5"),
    ("harness/decision.py", b"!= implementation_fingerprint()", b"== implementation_fingerprint()"),
    ("harness/context/build.py", b'.strip()', b'.rstrip()'),
    ("harness/transport/openai.py", b'"store": False', b'"store": True'),
    ("harness/money.py", b"total + amount <= cap", b"total + amount < cap"),
    ("service.py", b'raise ValueError("ONE_OVERRIDE_REQUIRED")', b'return None'),
    ("workbench/branches.py", b'if calibration:', b'if not calibration:'),
    ("workbench/budget_ledger.py", b'calls >= 0', b'calls > 0'),
    ("workbench/budget_continuation.py", b'combined_cap <= old_cap', b'combined_cap < old_cap'),
    ("evidence/recovery.py", b'if compatibility is None:', b'if compatibility is not None:'),
    ("evidence/provenance.py", b'if raw is None:', b'if raw is not None:'),
    ("evidence/lock.py", b'raise ', b'return '),
], indirect=True, ids=lambda case: case[0])
def test_changed_native_or_agent_execution_refuses_migration(historical):
    h, parent, old_hash, sources = historical
    # This must reach the execution/native comparison, not fail the full hash.
    assert fingerprint_sources(sources) == old_hash
    assert read_checkpoint(h.store, parent, 0)["implementation_hash"] == old_hash
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    assert not preview["available"] and preview["reason"] == "RESTORE_SOURCE_INCOMPATIBLE"
    assert len(h.games) == 1 and len(h.store.list_episodes()) == 1


def test_compatibility_revalidates_current_source_and_bound_protocol(historical):
    h, parent, old_hash, _ = historical
    bundle = freeze.read_protocol(h.store, read_checkpoint(h.store, parent, 0))
    proof = compatibility.prepare_compatibility({old_hash}, bundle, game_kind="synthetic")
    changed = deepcopy(proof)
    changed["accepted_implementation_hash"] = "f" * 64
    with pytest.raises(ValueError, match="RESTORE_COMPATIBILITY_STALE"):
        compatibility.validate_compatibility(changed)
    changed = deepcopy(proof)
    changed["source_revisions"] = {"a" * 64: "1" * 40}
    with pytest.raises(ValueError, match="RESTORE_SOURCE_INCOMPATIBLE"):
        compatibility.validate_compatibility(changed)


def test_receipt_cannot_be_relabelled_for_a_different_game_kind(historical):
    h, parent, _, _ = historical
    service = h.service()
    plan = restore_preview(service, parent, paid_enabled=True)["plan"]
    child = start_restore(service, parent, request_for(plan), paid_enabled=True)
    finish(service)
    checkpoint = read_checkpoint(h.store, child, 0)
    checkpoint["game"]["kind"] = "native"
    with pytest.raises(ValueError, match="RESTORE_SOURCE_INCOMPATIBLE"):
        freeze.restore_protocol(h.store, checkpoint)


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


@pytest.mark.parametrize("path", [
    "service_restore.py", "workbench/restoration.py", "workbench/restore_ledger.py",
    "evidence/compatibility.py", "evidence/execution_identity.py", "evidence/identity_migrations.py",
])
def test_restore_policy_and_compatibility_changes_invalidate_source_identity(path):
    sources = source_files(ROOT)
    before = fingerprint_sources(sources)
    sources["src/balatro_horizons/" + path] += b"\n# source identity mutation\n"
    assert fingerprint_sources(sources) != before


def test_branch_inherits_restore_proof_but_old_root_branch_still_refuses(historical):
    h, parent, old_hash, _ = historical
    original = {p: p.read_bytes() for p in h.store.episode_path(parent, True).rglob("*")
                if p.is_file() and p.name != "spending.json"}
    journal = h.store.episode_path(parent) / "events.jsonl"
    original[journal] = journal.read_bytes()
    with pytest.raises(ValueError, match="CHECKPOINT_IMPLEMENTATION_CHANGED"):
        h.service().branch(h.config, parent, 0, "agent_continue")
    service = h.service()
    plan = restore_preview(service, parent, paid_enabled=True)["plan"]
    child = start_restore(service, parent, request_for(plan), paid_enabled=True)
    finish(service)
    branch = service.branch(h.config, child, 0, "agent_continue")
    finish(service)
    assert h.store.summary(branch)["outcome"] == "WIN"
    checkpoint = read_checkpoint(h.store, branch, 0)
    assert checkpoint["source_compatibility"] == read_checkpoint(h.store, child, 0)["source_compatibility"]
    assert freeze.restore_protocol(h.store, checkpoint)["implementation_hash"] == old_hash
    assert {p: p.read_bytes() for p in original} == original
