#!/usr/bin/env python3
"""Explicitly accept a tested harness over certified native components.

No Windows reads, game launches, paid calls, or checkpoint recertification.
Historical artifacts keep their original implementation identity and provenance.
By default native bytes must match; --startup-report permits a narrowly checked
connection/startup patch with fresh unpaid native evidence for the changed layer.
"""

import argparse
import io
import json
import subprocess
import tarfile
import uuid
from pathlib import Path

from balatro_horizons.engine.provenance import (
    fingerprint_sources,
    native_components,
    source_files,
)
from balatro_horizons.storage.journal import atomic_json, digest, now


def require(condition, code):
    if not condition:
        raise ValueError(code)


def native_path(path):
    path = path.resolve()
    require(not path.is_relative_to('/mnt'), 'MOUNTED_PATH_OUTSIDE_SCOPE')
    return path


def revision_sources(root, revision):
    commit = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', '--verify', revision + '^{commit}'], text=True
    ).strip()
    archive = subprocess.check_output(
        ['git', '-C', str(root), 'archive', commit, '--', 'src/balatro_horizons']
    )
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        for member in contents:
            if member.isdir():
                continue
            require(member.isfile(), 'NONREGULAR_SOURCE_FILE')
            if member.name.endswith('.py'):
                sources[member.name] = contents.extractfile(member).read()
    return commit, sources


def prepare(root, candidate, baseline, offline_report, startup_report=None):
    root, candidate = native_path(root), native_path(candidate)
    old = json.loads((root/'private/capability-certificate.json').read_text())
    require(old.get('status') == 'passed', 'NATIVE_EVIDENCE_NOT_PASSED')
    identifier = old.get('certificate_id', '')
    require(len(identifier) == 32 and all(c in '0123456789abcdef' for c in identifier),
            'INVALID_CERTIFICATE_ID')
    original = json.loads((root/'private'/f'capability-record-{identifier}.json').read_text())
    require(original == old, 'ACTIVE_CERTIFICATE_RECORD_MISMATCH')
    commit, before = revision_sources(root, baseline)
    baseline_hash = fingerprint_sources(before)
    require(old.get('accepted_implementation_hash', old.get('implementation_hash')) == baseline_hash,
            'BASELINE_NOT_CERTIFIED')
    before_native = native_components(before)
    after = source_files(candidate)
    startup = None
    if startup_report is None:
        require(native_components(after) == before_native, 'NATIVE_COMPONENTS_CHANGED')
    else:
        from startup_scope import require_startup_scope

        require_startup_scope(before, after)
        startup = json.loads(native_path(startup_report).read_text())
        require(startup.get('suite') == 'startup_lifecycle' and startup.get('status') == 'passed'
                and startup.get('implementation_hash') == fingerprint_sources(after)
                and startup.get('native_implementation_hash') == digest(native_components(after))
                and startup.get('parent_certificate_hash') == digest(old)
                and startup.get('environment_hash') == old['environment_hash']
                and startup.get('profile_matches') is True and startup.get('native_launches') == 1
                and startup.get('provider_calls') == 0 and startup.get('cost_usd') == 0,
                'MATCHING_STARTUP_VERIFICATION_REQUIRED')
        require(set(startup.get('runs', {})) == {'smoke', 'pilot'} and all(
            run.get('outcome') in ('WIN', 'GAME_LOSS') and run.get('committed_actions', 0) > 0
            and run.get('provider_calls') == 0 and run.get('cost_usd') == 0
            for run in startup['runs'].values()), 'MATCHING_STARTUP_RUNS_REQUIRED')
    native_hash = digest(before_native)
    require(old.get('native_implementation_hash', native_hash) == native_hash,
            'BASELINE_NATIVE_MANIFEST_MISMATCH')
    lock = json.loads((root/'private/environment.lock.json').read_text())
    require(old['environment_hash'] == digest(lock), 'NATIVE_ENVIRONMENT_CHANGED')
    evidence = json.loads((root/'reports/verification/native-evidence.json').read_text())
    require(evidence.get('environment_hash') == old['environment_hash'], 'NATIVE_EVIDENCE_ENVIRONMENT_MISMATCH')
    require(evidence.get('accepted_implementation_hash', evidence.get('implementation_hash')) == baseline_hash,
            'NATIVE_EVIDENCE_SOURCE_MISMATCH')
    require(all((root/artifact).is_file() for entry in evidence.values()
                if isinstance(entry, dict) and entry.get('evidence_kind') == 'NATIVE'
                for artifact in entry.get('artifacts', [])), 'NATIVE_ARTIFACT_MISSING')
    source = fingerprint_sources(after)
    report = json.loads(native_path(offline_report).read_text())
    require(report.get('status') == 'passed' and report.get('implementation_hash') == source
            and report.get('suite') == 'check_offline', 'MATCHING_OFFLINE_CHECKS_REQUIRED')
    reuse = {
        'kind': 'unchanged_native_components', 'baseline_revision': commit,
        'parent_certificate_id': identifier, 'parent_certificate_hash': digest(old),
        'offline_report_hash': digest(report), 'native_component_manifest': before_native,
        'native_launches': 0, 'checkpoint_certificates_migrated': False,
    }
    if startup:
        native_hash = digest(native_components(after))
        reuse.update(kind='startup_connection_only', native_launches=1,
                     startup_verification=startup, startup_report_hash=digest(startup))
    cert = {
        **old, 'schema_version': 2, 'certificate_id': uuid.uuid4().hex, 'created_at': now(),
        'accepted_implementation_hash': source, 'native_implementation_hash': native_hash,
        'validation_kind': 'startup_compatibility' if startup else 'harness_compatibility', 'reuse': reuse,
        'native_validation_created_at': old.get('native_validation_created_at', old['created_at']),
    }
    return cert, {**evidence, 'accepted_implementation_hash': source,
                  'native_implementation_hash': native_hash, 'reuse': reuse}


def activate(root, candidate, cert, evidence):
    root, candidate = native_path(root), native_path(candidate)
    require(fingerprint_sources(source_files(root)) == cert['accepted_implementation_hash'],
            'CANDIDATE_NOT_INSTALLED')
    require(fingerprint_sources(source_files(candidate)) == cert['accepted_implementation_hash'],
            'CANDIDATE_CHANGED')
    active = json.loads((root/'private/capability-certificate.json').read_text())
    require(digest(active) == cert['reuse']['parent_certificate_hash'], 'ACTIVE_CERTIFICATE_CHANGED')
    require(digest(json.loads((root/'private/environment.lock.json').read_text())) == cert['environment_hash'],
            'NATIVE_ENVIRONMENT_CHANGED')
    record = root/'private'/('capability-record-' + cert['certificate_id'] + '.json')
    atomic_json(record, cert, immutable=True)
    # Preserve the exact reused evidence separately; only the selection is mutable.
    atomic_json(root/'reports/verification'/('native-evidence-reuse-' + cert['certificate_id'] + '.json'),
                evidence, immutable=True)
    atomic_json(root/'reports/verification/native-evidence.json', evidence)
    atomic_json(root/'private/capability-certificate.json', cert)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path, help='Live Linux workbench')
    parser.add_argument('--candidate', required=True, type=Path, help='Tested Linux source')
    parser.add_argument('--baseline', required=True, help='Previously accepted Git revision')
    parser.add_argument('--offline-report', required=True, type=Path)
    parser.add_argument('--startup-report', type=Path,
                        help='Explicit startup-only scope: requires fresh native startup/transport evidence')
    parser.add_argument('--apply', action='store_true', help='Activate only after installing the candidate while idle')
    args = parser.parse_args()
    try:
        cert, evidence = prepare(args.root, args.candidate, args.baseline, args.offline_report, args.startup_report)
        if args.apply:
            activate(args.root, args.candidate, cert, evidence)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{str(error) if str(error).isupper() else type(error).__name__}\n')
    print(json.dumps({'compatible': True, 'activated': args.apply,
                      'accepted_implementation_hash': cert['accepted_implementation_hash'],
                      'native_implementation_hash': cert['native_implementation_hash'],
                      'reused_certificate': cert['reuse']['parent_certificate_id'],
                      'native_launches': cert['reuse']['native_launches'], 'checkpoint_certificates_migrated': False}, sort_keys=True))


if __name__ == '__main__':
    main()
