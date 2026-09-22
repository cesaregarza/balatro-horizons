"""Credential-write fault tests use disposable Linux files and fake values only."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from balatro_horizons.cli import credentials


@pytest.fixture
def files(tmp_path):
    source = tmp_path / "source.env"
    source.write_text("OPENAI_API_KEY=fake-private-value\n")
    root, units = tmp_path / "repo", tmp_path / "units"
    target = root / "private/providers.env"
    target.parent.mkdir(parents=True)
    target.write_text("OPENAI_API_KEY=original-private-value\n")
    dropin = units / "balatro-horizons.service.d/20-provider-environment.conf"
    return SimpleNamespace(root=root, unit_dir=units, source=source, apply=True), target, dropin


@pytest.mark.parametrize("blocked", ["symlink", "parent-symlink", "directory", "parent-file"])
def test_both_destinations_preflight_before_credentials_change(files, tmp_path, blocked):
    args, target, dropin = files
    original = target.read_bytes()
    if blocked == "parent-file":
        dropin.parent.parent.mkdir(parents=True)
        dropin.parent.write_text("untouched")
    elif blocked == "parent-symlink":
        destination = tmp_path / "untouched-dir"
        destination.mkdir()
        dropin.parent.parent.mkdir(parents=True)
        dropin.parent.symlink_to(destination)
    else:
        dropin.parent.mkdir(parents=True)
        if blocked == "symlink":
            destination = tmp_path / "untouched"
            destination.write_text("original")
            dropin.symlink_to(destination)
        else:
            dropin.mkdir()
    with pytest.raises(ValueError, match="SYMLINK_DESTINATION_FORBIDDEN|CREDENTIAL_DESTINATION_UNSAFE"):
        credentials.configure(args.root, args.unit_dir, args.source, apply=True)
    assert target.read_bytes() == original
    if blocked == "symlink":
        assert destination.read_text() == "original"
    elif blocked == "parent-symlink":
        assert list(destination.iterdir()) == []


@pytest.mark.parametrize("failed_replace", [1, 2])
def test_replace_failure_reports_exact_partial_state(files, monkeypatch, capsys, failed_replace):
    args, target, dropin = files
    original = target.read_bytes()
    replace = credentials.os.replace
    calls = 0
    def fail_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == failed_replace:
            raise OSError("fake-private-value at " + str(destination))
        replace(source, destination)
    monkeypatch.setattr(credentials.os, "replace", fail_replace)
    assert credentials.run(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == ("CREDENTIAL_DESTINATION_UNSAFE" if failed_replace == 1 else "CREDENTIAL_APPLY_INCOMPLETE")
    assert target.read_bytes() == (original if failed_replace == 1 else args.source.read_bytes())
    assert not dropin.exists()
    if failed_replace == 2:
        assert result == {"error": "CREDENTIAL_APPLY_INCOMPLETE", "reason": "CREDENTIAL_DESTINATION_UNSAFE",
                          "applied": False, "credentials_written": True, "drop_in_written": False,
                          "service_restart_required": True, "provider_calls": 0}
    assert "private-value" not in json.dumps(result) and str(args.root) not in json.dumps(result)
    assert list(target.parent.iterdir()) == [target]
    assert not dropin.parent.exists() or list(dropin.parent.iterdir()) == []


@pytest.mark.parametrize("failed_chmod", ["credentials", "dropin"])
def test_failure_after_rename_still_records_replacement(files, monkeypatch, capsys, failed_chmod):
    args, target, dropin = files
    fail_at = target if failed_chmod == "credentials" else dropin
    chmod = Path.chmod
    def fail_chmod(path, *values, **options):
        if path == fail_at:
            raise OSError("private diagnostic")
        return chmod(path, *values, **options)
    monkeypatch.setattr(Path, "chmod", fail_chmod)
    assert credentials.run(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "CREDENTIAL_APPLY_INCOMPLETE"
    assert result["credentials_written"] is True
    assert result["drop_in_written"] is (failed_chmod == "dropin")
    assert target.read_bytes() == args.source.read_bytes()
    assert "private diagnostic" not in json.dumps(result)


def test_successful_retry_after_partial_apply_keeps_permissions(files, monkeypatch, capsys):
    args, target, dropin = files
    write = credentials.atomic_private
    def first_attempt(path, data):
        if path == dropin:
            raise ValueError("CREDENTIAL_DESTINATION_UNSAFE")
        return write(path, data)
    monkeypatch.setattr(credentials, "atomic_private", first_attempt)
    assert credentials.run(args) == 1
    monkeypatch.setattr(credentials, "atomic_private", write)
    args.source = None
    assert credentials.run(args) == 0
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert results[0]["credentials_written"] is True and results[1]["applied"] is True
    assert results[1]["drop_in_written"] is True
    assert target.stat().st_mode & 0o777 == dropin.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == dropin.parent.stat().st_mode & 0o777 == 0o700
    assert dropin.read_text() == f"[Service]\nEnvironmentFile=\nEnvironmentFile={target}\n"


@pytest.mark.parametrize("case, code", [
    ("unsupported", "UNSUPPORTED_ENVIRONMENT_PATH"),
    ("mounted", "NATIVE_LINUX_PATH_REQUIRED"),
    ("unsafe", "CREDENTIAL_DESTINATION_UNSAFE"),
    ("unreadable", "CREDENTIAL_SOURCE_UNREADABLE"),
    ("empty", "PROVIDER_CREDENTIAL_MISSING"),
])
def test_named_refusal_codes_leak_neither_values_nor_paths(files, capsys, case, code):
    args, target, dropin = files
    original = target.read_bytes()
    if case == "unsupported":
        args.root = args.root / "unsupported path"
    elif case == "mounted":
        args.source = Path("/mnt/c/not-read.env")
    elif case == "unsafe":
        dropin.parent.mkdir(parents=True)
        dropin.mkdir()
    elif case == "unreadable":
        args.source = args.source.with_name("absent.env")
    else:
        args.source.write_text("# no credential\n")
    assert credentials.run(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"error": code}
    assert target.read_bytes() == original
    assert "private-value" not in json.dumps(result) and str(args.root) not in json.dumps(result)
