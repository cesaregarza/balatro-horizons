import json
import subprocess

import pytest

from balatro_horizons.cli import install_candidate


@pytest.fixture
def prepared(tmp_path):
    module = install_candidate
    root, candidate = tmp_path / 'live', tmp_path / 'candidate'
    def git(*args):
        subprocess.run(['git', '-c', 'commit.gpgsign=false', *map(str, args)],
                       check=True, capture_output=True)
    git('init', root)
    git('-C', root, 'config', 'user.name', 'Fixture')
    git('-C', root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'source.py').write_text('old\n')
    git('-C', root, 'add', '.')
    git('-C', root, 'commit', '-m', 'Fixture base')
    git('-C', root, 'worktree', 'add', '-b', 'candidate', candidate)
    (candidate / 'source.py').write_text('new\n')
    (candidate / 'added.py').write_text('added\n')
    git('-C', candidate, 'add', '.')
    git('-C', candidate, 'commit', '-m', 'Fixture change')
    return module, root, candidate, tmp_path / 'manifest.json', git


def test_prepare_manifest_and_install_rollback_round_trip(prepared, tmp_path):
    module, root, candidate, manifest, _ = prepared
    result = module.prepare(root, candidate, manifest)
    (root/'private').mkdir()
    for name in ('environment.lock.json', 'rules.json'):
        (root/'private'/name).write_text('original')
    assert result['files'] == 2
    rows = json.loads(manifest.read_text())
    assert rows[0]['path'] == 'added.py' and rows[0]['before_sha256'] is None
    (candidate / 'web/dist').mkdir(parents=True)
    (candidate / 'web/dist/index.html').write_text('fixture build')
    backup = tmp_path / 'backup'
    module.install(root, candidate, manifest, backup)
    assert (root / 'source.py').read_text() == 'new\n'
    assert (root / 'added.py').read_text() == 'added\n'
    for name in ('environment.lock.json', 'rules.json'):
        (root/'private'/name).write_text('installed')
    module.rollback(root, backup)
    assert (root / 'source.py').read_text() == 'old\n'
    assert not (root / 'added.py').exists()
    for name in ('environment.lock.json', 'rules.json'):
        assert (root/'private'/name).read_text() == 'original'


@pytest.mark.parametrize('failure', ['dirty', 'removed', 'symlink'])
def test_prepare_rejects_unsupported_changes_before_writing(prepared, failure):
    module, root, candidate, manifest, git = prepared
    if failure == 'dirty':
        (candidate / 'source.py').write_text('uncommitted')
    else:
        (candidate / 'source.py').unlink()
        if failure == 'symlink':
            (candidate / 'source.py').symlink_to('added.py')
        git('-C', candidate, 'add', '.')
        git('-C', candidate, 'commit', '-m', 'Unsupported fixture change')
    with pytest.raises(ValueError):
        module.prepare(root, candidate, manifest)
    assert not manifest.exists()


def test_runtime_snapshot_is_complete_without_secrets_or_generated_data(prepared, tmp_path, monkeypatch):
    module, *_ = prepared
    runtime = tmp_path/'runtime'
    (runtime/'Mods/bot').mkdir(parents=True)
    for name in ('horizons-owned.json', 'bridge.ps1', 'version.dll', 'environment.lock.json',
                 'Mods/bot/instrument.lua', 'token.txt'):
        (runtime/name).write_text(name)
    (runtime/'Mods/lovely').mkdir()
    (runtime/'Mods/lovely/generated.lua').write_text('generated')
    monkeypatch.setattr(
        module,
        'Environment',
        lambda: type('RuntimeConfig', (), {'runtime': str(runtime)})(),
    )
    backup = tmp_path/'snapshot'
    result = module.snapshot_runtime(backup)
    hashes = json.loads((backup/'manifest.json').read_text())
    assert result['files'] == 5 and result['native_launches'] == 0
    assert 'token.txt' not in hashes and 'Mods/lovely/generated.lua' not in hashes
    for name, checksum in hashes.items():
        assert (backup/'files'/name).read_bytes() == (runtime/name).read_bytes()
        assert module.digest(backup/'files'/name) == checksum
    with pytest.raises(FileExistsError):
        module.snapshot_runtime(backup)
    (runtime/'Mods/bot/link').symlink_to(runtime/'token.txt')
    with pytest.raises(ValueError, match='REGULAR_FILES_REQUIRED'):
        module.snapshot_runtime(tmp_path/'unsafe')
    assert not (tmp_path/'unsafe').exists()


def test_prepare_can_preserve_only_unrelated_local_changes(prepared):
    module, root, candidate, manifest, _ = prepared
    (root/'unrelated.txt').write_text('preserve me')
    with pytest.raises(ValueError, match='WORKTREE_NOT_CLEAN'):
        module.prepare(root, candidate, manifest)
    module.prepare(root, candidate, manifest, allow_unrelated_changes=True)
    assert {row['path'] for row in json.loads(manifest.read_text())} == {'source.py', 'added.py'}
    manifest.unlink()
    (root/'source.py').write_text('local edit')
    with pytest.raises(ValueError, match='CANDIDATE_OVERLAPS_LOCAL_CHANGES'):
        module.prepare(root, candidate, manifest, allow_unrelated_changes=True)
    assert not manifest.exists()
    (root/'source.py').write_text('old\n')
    (root/'added.py').write_text('untracked collision')
    with pytest.raises(ValueError, match='CANDIDATE_OVERLAPS_LOCAL_CHANGES'):
        module.prepare(root, candidate, manifest, allow_unrelated_changes=True)


def test_snapshot_then_git_fast_forward_keeps_unrelated_files(prepared, tmp_path):
    module, root, candidate, manifest, git = prepared
    (root/'unrelated.txt').write_text('preserve me')
    (root/'web/dist').mkdir(parents=True)
    (root/'web/dist/index.html').write_text('previous frontend')
    module.prepare(root, candidate, manifest, allow_unrelated_changes=True)
    backup = tmp_path/'backup'
    module.install(root, candidate, manifest, backup, snapshot_only=True)
    assert (root/'source.py').read_text() == 'old\n'
    assert not (root/'added.py').exists()
    assert (backup/'files/source.py').read_text() == 'old\n'
    git('-C', root, 'merge', '--ff-only', 'candidate')
    assert (root/'source.py').read_text() == 'new\n'
    assert (root/'unrelated.txt').read_text() == 'preserve me'
    module.rollback(root, backup)
    assert (root/'source.py').read_text() == 'old\n'
    assert (root/'unrelated.txt').read_text() == 'preserve me'
