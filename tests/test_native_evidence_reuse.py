"""Evidence reuse needs identical native source and fresh harness validation."""

import importlib.util
import json
import shutil
from copy import deepcopy

import pytest

from balatro_horizons.config import ROOT, Environment
from balatro_horizons.engine import certification, provenance
from balatro_horizons.engine.native import NativeFailure
from balatro_horizons.storage.journal import digest

SPEC = importlib.util.spec_from_file_location('reuse_native_evidence', ROOT/'scripts/reuse_native_evidence.py')
reuse = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reuse)


def test_archived_full_fingerprint_matches_working_tree_identity():
    # The old proof must be verified from bytes, without importing old Python.
    revision, old = reuse.revision_sources(ROOT, 'HEAD')
    assert len(revision) == 40
    assert 'src/balatro_horizons/engine/native.py' in old
    assert provenance.fingerprint_sources(provenance.source_files(ROOT)) == provenance.implementation_fingerprint()


def test_harness_only_edits_change_full_identity_but_not_native_components():
    before = provenance.source_files(ROOT)
    after = deepcopy(before)
    after['src/balatro_horizons/agents/notebook.py'] += b'\n# changed notebook policy\n'
    after['src/balatro_horizons/runner.py'] += b'\n# changed harness orchestration\n'
    assert provenance.fingerprint_sources(before) != provenance.fingerprint_sources(after)
    assert provenance.native_components(before) == provenance.native_components(after)


@pytest.mark.parametrize('path', [
    'engine/native.py', 'engine/native_state.py', 'engine/replay.py',
    'engine/continuation_probe.py', 'contracts.py',
    'observations/projection.py', 'actions/validation.py', 'storage/journal.py',
    'engine/new_native_module.py',
])
def test_native_edits_additions_and_removals_invalidate_compatibility(path):
    source = provenance.source_files(ROOT)
    before = provenance.native_components(source)
    key = 'src/balatro_horizons/' + path
    source[key] = source.get(key, b'') + b'\n# changed native code\n'
    assert provenance.native_components(source) != before
    del source[key]
    if path != 'engine/new_native_module.py':
        assert provenance.native_components(source) != before


def test_config_native_dependencies_are_tracked_but_model_budgets_are_separate():
    source = provenance.source_files(ROOT)
    key = 'src/balatro_horizons/config.py'
    before = provenance.native_components(source)
    source[key] = source[key].replace(b'WORKING_MEMORY_DECISIONS = 3', b'WORKING_MEMORY_DECISIONS = 4')
    assert provenance.native_components(source) == before
    source[key] = source[key].replace(b'port: int = Field(default=12346', b'port: int = Field(default=12347')
    assert provenance.native_components(source) != before
    source[key] = source[key].replace(b'port: int = Field(default=12347', b'port: int = Field(default=NATIVE_PORT')
    source[key] += b'\nNATIVE_PORT = 12347\n'
    before = provenance.native_components(source)
    source[key] = source[key].replace(b'NATIVE_PORT = 12347', b'NATIVE_PORT = 12348')
    assert provenance.native_components(source) != before


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
    notebook = candidate/'src/balatro_horizons/agents/notebook.py'
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
    with pytest.raises(ValueError, match='CANDIDATE_NOT_INSTALLED'):
        reuse.activate(root, candidate, cert, evidence)
    shutil.copytree(candidate/'src', root/'src', dirs_exist_ok=True)
    reuse.activate(root, candidate, cert, evidence)
    assert json.loads((root/'private/capability-certificate.json').read_text()) == cert
    assert json.loads((root/'private'/('capability-record-' + old['certificate_id'] + '.json')).read_text()) == old


@pytest.mark.parametrize('failure,code', [
    ('native', 'NATIVE_COMPONENTS_CHANGED'), ('report', 'MATCHING_OFFLINE_CHECKS_REQUIRED'),
    ('environment', 'NATIVE_ENVIRONMENT_CHANGED'), ('baseline', 'BASELINE_NOT_CERTIFIED'),
    ('record', 'ACTIVE_CERTIFICATE_RECORD_MISMATCH'),
])
def test_reuse_fails_closed_without_changing_active_certificate(migration, failure, code):
    root, candidate, report, old = migration
    if failure == 'native':
        path = candidate/'src/balatro_horizons/engine/native.py'
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
    agent = candidate/'src/balatro_horizons/agents/notebook.py'
    agent.write_bytes(agent.read_bytes() + b'\n# another unaccepted harness change\n')
    with pytest.raises(NativeFailure, match='NATIVE_CAPABILITY_CERTIFICATE_MISMATCH'):
        certification.require_environment_certificate({'test': 'runtime'}, Environment())


def test_old_checkpoint_source_identity_still_fails_closed(migration, monkeypatch):
    from balatro_horizons.agents.frozen import FROZEN_INTERFACE, restore_protocol
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
