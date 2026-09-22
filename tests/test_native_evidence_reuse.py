"""Evidence reuse needs identical native source and fresh harness validation."""

import json
import shutil
from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT, Environment
from balatro_horizons.evidence import certification, provenance, reuse
from balatro_horizons.game.session import NativeFailure
from balatro_horizons.storage.journal import digest


def test_archived_full_fingerprint_matches_working_tree_identity():
    # A committed proof must be verified from bytes, without importing Python.
    revision, old = reuse.revision_sources(ROOT, 'HEAD')
    assert len(revision) == 40
    assert 'src/balatro_horizons/config.py' in old
    assert provenance.fingerprint_sources(provenance.source_files(ROOT)) == provenance.implementation_fingerprint()


def test_every_implementation_fingerprint_file_exists():
    base = ROOT / "src/balatro_horizons"
    assert all((base / path).is_file() for path in provenance.IMPLEMENTATION_FILES)


def test_certified_revision_resolves_an_exact_source_identity():
    revision, source = reuse.revision_sources(ROOT, 'HEAD')
    expected = provenance.fingerprint_sources(source)
    assert reuse.certified_revision(ROOT, expected, ['HEAD']) == revision
    with pytest.raises(ValueError, match='CERTIFIED_BASELINE_REVISION_NOT_FOUND'):
        reuse.certified_revision(ROOT, 'absent', ['HEAD'])


def test_harness_only_edits_change_full_identity_but_not_native_game_identity():
    before = provenance.source_files(ROOT)
    after = deepcopy(before)
    after['src/balatro_horizons/harness/context/memory.py'] += b'\n# changed notebook policy\n'
    after['src/balatro_horizons/harness/loop.py'] += b'\n# changed harness orchestration\n'
    assert provenance.fingerprint_sources(before) != provenance.fingerprint_sources(after)
    assert (
        provenance.native_implementation_fingerprint(before)
        == provenance.native_implementation_fingerprint(after)
    )


@pytest.mark.parametrize('path', [
    'service_execution.py',
    'workbench/budget_continuation.py',
    'workbench/budget_ledger.py',
])
def test_full_identity_only_files_do_not_change_native_identity(path):
    source = provenance.source_files(ROOT)
    key = 'src/balatro_horizons/' + path
    baseline = provenance.fingerprint_sources(source)
    native = provenance.native_implementation_fingerprint(source)

    edited = {**source, key: source[key] + b'\n# budget identity change\n'}
    assert provenance.fingerprint_sources(edited) != baseline
    assert provenance.native_implementation_fingerprint(edited) == native

    deleted = {name: data for name, data in source.items() if name != key}
    assert provenance.fingerprint_sources(deleted) != baseline
    assert provenance.native_implementation_fingerprint(deleted) == native

    added = {**deleted, key: source[key] + b'\n# budget identity addition\n'}
    assert provenance.fingerprint_sources(added) != baseline
    assert provenance.native_implementation_fingerprint(added) == native


@pytest.mark.parametrize('path', [
    'game/session.py', 'game/state/normalize.py', 'game/replay.py',
    'evidence/continuation_probe.py', 'game/new_native_module.py',
    'contracts.py', 'observations/projection.py', 'actions/validation.py', 'storage/journal.py',
])
def test_native_edits_additions_and_removals_invalidate_compatibility(path):
    source = provenance.source_files(ROOT)
    before = provenance.native_implementation_fingerprint(source)
    key = 'src/balatro_horizons/' + path
    source[key] = source.get(key, b'') + b'\n# changed native code\n'
    assert provenance.native_implementation_fingerprint(source) != before
    del source[key]
    if path != 'game/new_native_module.py':
        assert provenance.native_implementation_fingerprint(source) != before


def test_fake_game_edits_do_not_change_native_identity():
    source = provenance.source_files(ROOT)
    before_native = provenance.native_implementation_fingerprint(source)
    before_full = provenance.fingerprint_sources(source)
    source["src/balatro_horizons/game/fake.py"] += b"\n# synthetic-only change\n"
    assert provenance.native_implementation_fingerprint(source) == before_native
    assert provenance.fingerprint_sources(source) != before_full


def test_game_environment_defaults_are_tracked_but_model_budgets_are_separate():
    source = provenance.source_files(ROOT)
    config_key = 'src/balatro_horizons/config.py'
    key = 'src/balatro_horizons/game/environment.py'
    before = provenance.native_implementation_fingerprint(source)
    source[config_key] = source[config_key].replace(
        b'WORKING_MEMORY_DECISIONS = 3', b'WORKING_MEMORY_DECISIONS = 4'
    )
    assert provenance.native_implementation_fingerprint(source) == before
    source[key] = source[key].replace(b'port: int = Field(default=12346', b'port: int = Field(default=12347')
    assert provenance.native_implementation_fingerprint(source) != before
    source[key] = source[key].replace(b'port: int = Field(default=12347', b'port: int = Field(default=NATIVE_PORT')
    source[key] += b'\nNATIVE_PORT = 12347\n'
    before = provenance.native_implementation_fingerprint(source)
    source[key] = source[key].replace(b'NATIVE_PORT = 12347', b'NATIVE_PORT = 12348')
    assert provenance.native_implementation_fingerprint(source) != before


@pytest.fixture
def migration(tmp_path, monkeypatch):
    root, candidate = tmp_path/'live', tmp_path/'candidate'
    source = provenance.source_files(ROOT)
    for directory in (root, candidate):
        for name, data in source.items():
            path = directory/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    baseline_hash = provenance.fingerprint_sources(source)
    old = {'schema_version': 1, 'certificate_id': 'a'*32, 'status': 'passed',
           'created_at': '2026-09-17T00:00:00Z', 'implementation_hash': baseline_hash,
           'environment_hash': digest({'test': 'runtime'}), 'configurations': [['RED', 'GOLD']]}
    (root/'private').mkdir()
    for name, data in [('capability-certificate.json', old),
                       ('capability-record-' + old['certificate_id'] + '.json', old),
                       ('environment.lock.json', {'test': 'runtime'})]:
        (root/'private'/name).write_text(json.dumps(data))
    (root/'reports/verification').mkdir(parents=True)
    evidence = {'implementation_hash': baseline_hash, 'environment_hash': old['environment_hash']}
    (root/'reports/verification/native-evidence.json').write_text(json.dumps(evidence))
    notebook = candidate/'src/balatro_horizons/harness/context/memory.py'
    notebook.write_bytes(notebook.read_bytes() + b'\n# tested harness update\n')
    report = tmp_path/'offline.json'
    report.write_text(json.dumps({'suite': 'check_offline', 'status': 'passed',
        'implementation_hash': provenance.fingerprint_sources(provenance.source_files(candidate))}))
    monkeypatch.setattr(reuse, 'revision_sources', lambda *args: ('b'*40, source))
    return root, candidate, report, old


def test_reuse_preserves_original_evidence_and_does_not_migrate_checkpoints(migration):
    root, candidate, report, old = migration
    cert, evidence = reuse.prepare(root, candidate, 'baseline', report)
    assert cert['implementation_hash'] == old['implementation_hash']
    assert cert['accepted_implementation_hash'] != old['implementation_hash']
    assert cert['reuse']['native_launches'] == 0
    assert cert['reuse']['checkpoint_certificates_migrated'] is False
    assert cert['reuse']['parent_certificate_hash'] == digest(old)
    assert cert['native_component_manifest'] == provenance.native_component_manifest(
        provenance.source_files(ROOT)
    )
    assert cert['reuse']['native_component_manifest'] == cert['native_component_manifest']
    with pytest.raises(ValueError, match='CANDIDATE_NOT_INSTALLED'):
        reuse.activate(root, candidate, cert, evidence)
    shutil.copytree(candidate/'src', root/'src', dirs_exist_ok=True)
    reuse.activate(root, candidate, cert, evidence)
    assert json.loads((root/'private/capability-certificate.json').read_text()) == cert
    assert json.loads((root/'private'/('capability-record-' + old['certificate_id'] + '.json')).read_text()) == old


@pytest.mark.parametrize('failure,code', [
    ('native', 'NATIVE_GAME_SOURCE_CHANGED'), ('report', 'MATCHING_OFFLINE_CHECKS_REQUIRED'),
    ('environment', 'NATIVE_ENVIRONMENT_CHANGED'), ('baseline', 'BASELINE_NOT_CERTIFIED'),
    ('record', 'ACTIVE_CERTIFICATE_RECORD_MISMATCH'),
])
def test_reuse_fails_closed_without_changing_active_certificate(migration, failure, code):
    root, candidate, report, old = migration
    if failure == 'native':
        path = candidate/'src/balatro_horizons/game/session.py'
        path.write_bytes(path.read_bytes() + b'\n# changed\n')
    elif failure == 'report':
        report.write_text('{}')
    elif failure == 'environment':
        (root/'private/environment.lock.json').write_text('{}')
    else:
        changed = {**old, 'implementation_hash': 'wrong'}
        (root/'private/capability-certificate.json').write_text(json.dumps(changed))
        if failure == 'baseline':
            (root/'private'/('capability-record-' + old['certificate_id'] + '.json')).write_text(json.dumps(changed))
    before = (root/'private/capability-certificate.json').read_bytes()
    with pytest.raises(ValueError, match=code):
        reuse.prepare(root, candidate, 'baseline', report)
    assert (root/'private/capability-certificate.json').read_bytes() == before


def test_native_gate_requires_both_native_identity_and_explicit_harness_acceptance(migration, monkeypatch):
    root, candidate, report, _ = migration
    cert, _ = reuse.prepare(root, candidate, 'baseline', report)
    monkeypatch.setattr(certification, 'ROOT', root)
    monkeypatch.setattr(provenance, 'ROOT', candidate)
    path = root/'private/capability-certificate.json'
    path.write_text(json.dumps(cert))
    certification.require_environment_certificate({'test': 'runtime'}, Environment())
    for field in ('native_implementation_hash', 'accepted_implementation_hash', 'environment_hash'):
        bad = {**cert, field: 'wrong'}
        path.write_text(json.dumps(bad))
        with pytest.raises(NativeFailure, match='NATIVE_CAPABILITY_CERTIFICATE_MISMATCH'):
            certification.require_environment_certificate({'test': 'runtime'}, Environment())
    path.write_text(json.dumps(cert))
    agent = candidate/'src/balatro_horizons/harness/context/memory.py'
    agent.write_bytes(agent.read_bytes() + b'\n# another unaccepted harness change\n')
    with pytest.raises(NativeFailure, match='NATIVE_CAPABILITY_CERTIFICATE_MISMATCH'):
        certification.require_environment_certificate({'test': 'runtime'}, Environment())


def test_old_checkpoint_source_identity_still_fails_closed(migration, monkeypatch):
    from balatro_horizons.harness.context.freeze import FROZEN_INTERFACE, restore_protocol
    from balatro_horizons.storage.journal import Store

    root, candidate, _, old = migration
    monkeypatch.setattr(provenance, 'ROOT', candidate)
    store = Store(root/'data')
    eid = store.create({'evidence_kind': 'fixture'}, {})
    bundle = {
        'version': 'agent-protocol-v1',
        'interface': FROZEN_INTERFACE,
        'implementation_hash': old['implementation_hash'],
    }
    store.private_json(eid, 'agent-protocol.json', bundle)
    checkpoint = {'agent_protocol': {'episode_id': eid, 'hash': digest(deepcopy(bundle))}}
    with pytest.raises(ValueError, match='AGENT_PROTOCOL_IMPLEMENTATION_CHANGED'):
        restore_protocol(store, checkpoint)
