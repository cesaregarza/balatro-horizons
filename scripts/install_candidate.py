#!/usr/bin/env python3
"""Install a checksum-reviewed Linux candidate into an idle workbench; preserve rollback files.

The caller must stop the web service first and match the native capability before restarting,
through native verification or explicit compatible-harness evidence reuse.
This helper does not launch games, publish certificates, alter settings, or call paid providers.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

OWNED_RUNTIME = Path('/mnt/d/BalatroHorizonsRuntime')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def native(path):
    path = path.resolve()
    if path.is_relative_to('/mnt'):
        raise ValueError('MOUNTED_PATH_OUTSIDE_SCOPE')
    return path


def child(root, name):
    rel = Path(name)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('INVALID_MANIFEST_PATH')
    result = native(root / rel)
    if not result.is_relative_to(root):
        raise ValueError('PATH_OUTSIDE_ROOT')
    return result


def prepare(root, candidate, manifest, *, allow_unrelated_changes=False):
    """Freeze checksums; optionally retain live edits outside the candidate paths."""
    root, candidate, manifest = (native(p) for p in (root, candidate, manifest))
    def git(path, *args):
        return subprocess.check_output(['git', '-C', str(path), *args]).decode().strip()

    for path in (root, candidate):
        if Path(git(path, 'rev-parse', '--show-toplevel')).resolve() != path:
            raise ValueError('NOT_A_WORKTREE_ROOT')
        if git(path, 'status', '--porcelain') and not (path == root and allow_unrelated_changes):
            raise ValueError('WORKTREE_NOT_CLEAN')
    revision = git(candidate, 'rev-parse', 'HEAD')
    # Installation handles added/modified regular files, not removals or renames.
    removed = git(root, 'diff', '--no-renames', '--name-only', '--diff-filter=D',
                  'HEAD', revision)
    if removed:
        raise ValueError('REMOVALS_NOT_SUPPORTED')
    names = git(root, 'diff', '--no-renames', '--name-only', '-z', 'HEAD', revision)
    if allow_unrelated_changes:
        dirty = git(root, 'diff', '--name-only', '-z', 'HEAD')
        untracked = git(root, 'ls-files', '--others', '--exclude-standard', '-z')
        if set(names.split('\0')) & (set(dirty.split('\0')) | set(untracked.split('\0'))) - {''}:
            raise ValueError('CANDIDATE_OVERLAPS_LOCAL_CHANGES')
    rows = []
    for name in sorted(filter(None, names.split('\0'))):
        source, target = child(candidate, name), child(root, name)
        if not source.is_file() or (candidate / name).is_symlink() or (root / name).is_symlink():
            raise ValueError('REGULAR_FILES_REQUIRED')
        rows.append({'path': name, 'before_sha256': digest(target),
                     'after_sha256': digest(source)})
    if not rows:
        raise ValueError('NO_CANDIDATE_CHANGES')
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open('x') as stream:
        stream.write(json.dumps(rows, indent=2) + '\n')
    return {'candidate_commit': revision, 'files': len(rows), 'manifest': str(manifest)}


def install(root, candidate, manifest, backup, *, snapshot_only=False):
    rows = json.loads(manifest.read_text())
    if not rows or len({r['path'] for r in rows}) != len(rows):
        raise ValueError('INVALID_MANIFEST')
    # Verify every source/destination before any installation write.
    for row in rows:
        if digest(child(root, row['path'])) != row['before_sha256']:
            raise ValueError('DESTINATION_CHANGED: ' + row['path'])
        if digest(child(candidate, row['path'])) != row['after_sha256']:
            raise ValueError('CANDIDATE_CHANGED: ' + row['path'])
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    snapshots = [p for p in (root/'reports/verification').glob('*.json') if p.is_file()]
    snapshots += [root/'private/capability-certificate.json', root/'data/operator-settings.json',
                  root/'private/environment.lock.json', root/'private/rules.json']
    snapshots += [p for p in (root/'data/private_runs').glob('*/certificate-*.json')
                  if re.fullmatch(r'certificate-\d+\.json', p.name)]
    files = {r['path'] for r in rows if r['before_sha256'] is not None}
    files.update(str(p.relative_to(root)) for p in snapshots if p.is_file())
    metadata = {'rows': rows, 'snapshots': sorted(files), 'root': str(root)}
    (backup/'manifest.json').write_text(json.dumps(metadata, indent=2)+'\n')
    for rel in sorted(files):
        target = child(backup/'files', rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child(root, rel), target)
    if (root/'web/dist').exists():
        shutil.copytree(root/'web/dist', backup/'web-dist')
    if snapshot_only:
        print(json.dumps({'snapshot': str(backup), 'installed_files': 0}))
        return
    for row in rows:
        target = child(root, row['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child(candidate, row['path']), target)
        if digest(target) != row['after_sha256']:
            raise ValueError('INSTALL_CHECKSUM_MISMATCH')
    shutil.copytree(candidate/'web/dist', root/'web/dist', dirs_exist_ok=True)
    print(json.dumps({'installed_files':len(rows),'backup':str(backup),'capability_acceptance_required':True}))


def snapshot_runtime(backup):
    """Back up pinned instrumentation only; never copy saves, checkpoints or tokens."""
    backup = native(backup)
    runtime = OWNED_RUNTIME
    if runtime.resolve() != runtime or not (runtime/'horizons-owned.json').is_file():
        raise ValueError('OWNED_RUNTIME_REQUIRED')
    files = [runtime/name for name in
             ('horizons-owned.json', 'bridge.ps1', 'version.dll', 'environment.lock.json')]
    mods = runtime/'Mods'
    if not mods.is_dir() or mods.is_symlink():
        raise ValueError('MOD_DIRECTORY_REQUIRED')
    for path in sorted(mods.rglob('*')):
        if 'lovely' in path.relative_to(mods).parts:
            continue  # Generated logs and licensed-source dumps are not instrumentation.
        if path.is_symlink():
            raise ValueError('REGULAR_FILES_REQUIRED')
        if path.is_file():
            files.append(path)
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise ValueError('REGULAR_FILES_REQUIRED')
    hashes = {str(path.relative_to(runtime)): digest(path) for path in files}
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    for path in files:
        relative = path.relative_to(runtime)
        target = backup/'files'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if digest(target) != hashes[str(relative)] or digest(path) != hashes[str(relative)]:
            raise ValueError('RUNTIME_CHANGED_DURING_BACKUP')
    (backup/'manifest.json').write_text(json.dumps(hashes, indent=2, sort_keys=True)+'\n')
    return {'runtime_snapshot': str(backup), 'files': len(files), 'native_launches': 0}


def rollback(root, backup):
    saved = json.loads((backup/'manifest.json').read_text())
    if saved['root'] != str(root):
        raise ValueError('BACKUP_ROOT_MISMATCH')
    for row in saved['rows']:
        if digest(child(root, row['path'])) not in (row['before_sha256'],row['after_sha256']):
            raise ValueError('POST_INSTALL_EDIT: ' + row['path'])
    for rel in saved['snapshots']:
        shutil.copy2(child(backup/'files', rel), child(root, rel))
    for row in saved['rows']:
        if row['before_sha256'] is None:
            child(root, row['path']).unlink(missing_ok=True)
    if (backup/'web-dist').exists():
        shutil.copytree(backup/'web-dist', root/'web/dist', dirs_exist_ok=True)
    print(json.dumps({'restored':True,'root':str(root)}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--prepare', action='store_true',
                        help='Write a new checksum manifest from clean tracked checkouts; no install')
    parser.add_argument('--allow-unrelated-changes', action='store_true',
                        help='With --prepare, permit live edits only outside the candidate file set')
    parser.add_argument('--snapshot-only', action='store_true',
                        help='Validate the manifest and back up Linux files; do not install')
    parser.add_argument('--snapshot-runtime', action='store_true',
                        help='Back up owned Windows instrumentation to Linux --backup; no install or launch')
    args=parser.parse_args()
    if args.allow_unrelated_changes and not args.prepare:
        parser.error('--allow-unrelated-changes requires --prepare')
    if args.snapshot_only and (args.prepare or args.rollback or args.snapshot_runtime):
        parser.error('--snapshot-only cannot be combined with other modes')
    root = native(args.root)
    if args.snapshot_runtime:
        if not args.backup or args.prepare or args.rollback or args.candidate or args.manifest:
            parser.error('--snapshot-runtime requires only --root and --backup')
        print(json.dumps(snapshot_runtime(args.backup), sort_keys=True))
        return
    if args.prepare:
        if args.rollback or not args.candidate or not args.manifest:
            parser.error('--prepare requires --candidate and --manifest, without --rollback')
        print(json.dumps(prepare(root, args.candidate, args.manifest,
                                 allow_unrelated_changes=args.allow_unrelated_changes), sort_keys=True))
        return
    if not args.backup:
        parser.error('--backup is required for installation or rollback')
    backup = native(args.backup)
    if args.rollback:
        rollback(root, backup)
    else:
        if not args.candidate or not args.manifest:
            parser.error('--candidate and --manifest are required for installation')
        install(root, native(args.candidate), native(args.manifest), backup,
                snapshot_only=args.snapshot_only)


if __name__=='__main__':
    main()
