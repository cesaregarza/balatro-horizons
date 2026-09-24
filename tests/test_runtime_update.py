import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from balatro_horizons.cli import runtime_update as update
from balatro_horizons.game.environment import instrument_hash


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    baseline, candidate, runtime = (tmp_path / name for name in ("baseline", "candidate", "runtime"))
    for root in (baseline, candidate):
        (root / "private").mkdir(parents=True)
    (candidate / ".gitignore").write_text("private/environment.lock.json\n")
    (candidate / "native/patches").mkdir(parents=True)
    (runtime / "Mods/balatrobot").mkdir(parents=True)
    (runtime / "horizons-owned.json").write_text('{"purpose":"isolated-balatro-horizons"}\n')
    files = {"Balatro.exe": b"game", "version.dll": b"injector", "bridge.ps1": b"old bridge",
             "Mods/balatrobot/patch.lua": b"old patch"}
    for rel, content in files.items():
        path = runtime / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (runtime / "Mods/.git").mkdir()
    (runtime / "Mods/.git/ignored.lua").write_text("excluded from instrument hash")
    lock = {"game_sha256": sha(files["Balatro.exe"]), "injector_sha256": sha(files["version.dll"]),
            "bridge_sha256": sha(files["bridge.ps1"]), "mods_sha256": instrument_hash(runtime / "Mods"),
            "speed": 16, "other": "preserved"}
    (baseline / "private/environment.lock.json").write_text(json.dumps(lock))
    (runtime / "environment.lock.json").write_text(json.dumps(lock))
    (candidate / "private/environment.lock.json").write_text(json.dumps(lock))
    (candidate / "native/bridge.ps1").write_bytes(b"new bridge")
    (candidate / "native/patches/patch.lua").write_bytes(b"new patch")
    subprocess.run(["git", "init", str(candidate)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(candidate), "-c", "user.name=Test", "-c", "user.email=t@example.invalid",
                    "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(candidate), "-c", "user.name=Test", "-c", "user.email=t@example.invalid",
                    "commit", "-m", "fixture"], check=True, capture_output=True)
    def env():
        return type("Env", (), {"runtime": str(runtime)})()
    monkeypatch.setattr(update, "Environment", env)
    return baseline, candidate, runtime, tmp_path / "backup"


def test_dry_run_has_no_writes_and_apply_rollback_round_trip(fixture):
    baseline, candidate, runtime, backup = fixture
    before = (runtime / "bridge.ps1").read_bytes()
    plan = update.update(candidate, baseline, backup)
    assert len(plan["changes"]) == 2 and not backup.exists()
    assert (runtime / "bridge.ps1").read_bytes() == before
    update.update(candidate, baseline, backup, apply=True)
    assert (runtime / "bridge.ps1").read_bytes() == b"new bridge"
    assert json.loads((candidate / "private/environment.lock.json").read_text())["other"] == "preserved"
    assert update.rollback(backup)["restored"]
    assert (runtime / "bridge.ps1").read_bytes() == b"old bridge"
    assert (runtime / "Mods/balatrobot/patch.lua").read_bytes() == b"old patch"
    update.update(candidate, baseline, backup.parent / "backup-again", apply=True)
    assert (runtime / "bridge.ps1").read_bytes() == b"new bridge"


@pytest.mark.parametrize("failure", ["owner", "candidate_dirty", "baseline_drift", "candidate_lock_conflict", "target_drift"])
def test_refuses_unsafe_inputs_before_writing(fixture, failure):
    baseline, candidate, runtime, backup = fixture
    if failure == "owner":
        (runtime / "horizons-owned.json").write_text('{"purpose":"other"}')
    elif failure == "candidate_dirty":
        (candidate / "native/bridge.ps1").write_bytes(b"dirty")
    elif failure == "baseline_drift":
        (runtime / "bridge.ps1").write_bytes(b"drift")
    elif failure == "candidate_lock_conflict":
        (candidate / "private/environment.lock.json").write_text("{}")
    else:
        (runtime / "Mods/balatrobot/patch.lua").write_bytes(b"drift")
    with pytest.raises(ValueError):
        update.update(candidate, baseline, backup, apply=True)
    assert not backup.exists()


def test_symlinked_candidate_path_and_existing_backup_are_refused(fixture, tmp_path):
    baseline, candidate, runtime, backup = fixture
    external = tmp_path / "external.lua"
    external.write_text("patch")
    (candidate / "native/patches/linked.lua").symlink_to(external)
    subprocess.run(["git", "-C", str(candidate), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(candidate), "-c", "user.name=Test", "-c", "user.email=t@example.invalid",
                    "commit", "-m", "symlink"], check=True, capture_output=True)
    with pytest.raises(ValueError):
        update.update(candidate, baseline, backup)


def test_configured_runtime_bypasses_linux_mount_guard(monkeypatch):
    calls = []
    monkeypatch.setattr(update, "Environment", lambda: type("Env", (), {"runtime": "/mnt/d/CanonicalRuntime"})())
    monkeypatch.setattr(update, "native_path", lambda path: calls.append(path) or (_ for _ in ()).throw(AssertionError()))
    assert str(update._configured_runtime()) == "/mnt/d/CanonicalRuntime"
    assert calls == []


def test_existing_backup_is_refused(fixture):
    baseline, candidate, runtime, backup = fixture
    backup.mkdir()
    with pytest.raises(FileExistsError):
        update.update(candidate, baseline, backup, apply=True)


def test_runtime_lock_must_equal_baseline_before_verification_or_backup(fixture, monkeypatch):
    baseline, candidate, runtime, backup = fixture
    (runtime / "environment.lock.json").write_text("{}")
    monkeypatch.setattr(update, "verify_files", lambda *args: (_ for _ in ()).throw(AssertionError("must not verify")))
    with pytest.raises(ValueError, match="RUNTIME_LOCK_BASELINE_MISMATCH"):
        update.update(candidate, baseline, backup, apply=True)
    assert not backup.exists()


@pytest.mark.parametrize("relative", [
    "Balatro.exe", "version.dll", "environment.lock.json", "Mods/balatrobot/unchanged.lua",
])
def test_runtime_symlink_is_rejected_before_verify_files(fixture, monkeypatch, tmp_path, relative):
    baseline, candidate, runtime, backup = fixture
    link = runtime / relative
    link.unlink(missing_ok=True)
    link.symlink_to(tmp_path / "elsewhere.lua")
    monkeypatch.setattr(update, "verify_files", lambda *args: (_ for _ in ()).throw(AssertionError("must not verify")))
    with pytest.raises(ValueError, match="RUNTIME_SYMLINK_REFUSED"):
        update.update(candidate, baseline, backup, apply=True)
    assert not backup.exists()


def test_rollback_refuses_post_install_drift(fixture):
    baseline, candidate, runtime, backup = fixture
    update.update(candidate, baseline, backup, apply=True)
    (runtime / "bridge.ps1").write_bytes(b"operator edit")
    with pytest.raises(ValueError, match="POST_INSTALL_DRIFT"):
        update.rollback(backup)


@pytest.mark.parametrize("mutation, error", [
    ("traversal", "INVALID_RUNTIME_UPDATE_PATHS"),
    ("absolute", "INVALID_RUNTIME_UPDATE_PATHS"),
    ("duplicate", "INVALID_RUNTIME_UPDATE_PATHS"),
    ("runtime", "RUNTIME_RECEIPT_MISMATCH"),
    ("missing_backup_hash", "BACKUP_CHECKSUM_MISSING"),
])
def test_rollback_validates_receipt_paths_and_backup_hashes(fixture, mutation, error):
    baseline, candidate, runtime, backup = fixture
    update.update(candidate, baseline, backup, apply=True)
    receipt_path = backup / update.RECEIPT
    receipt = json.loads(receipt_path.read_text())
    if mutation == "traversal":
        receipt["changes"][0]["path"] = "../token.txt"
    elif mutation == "absolute":
        receipt["changes"][0]["path"] = "/bridge.ps1"
    elif mutation == "duplicate":
        receipt["changes"].append(dict(receipt["changes"][0]))
    elif mutation == "runtime":
        receipt["runtime"] = str(runtime / "other")
    else:
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop(receipt["changes"][0]["path"])
        manifest_path.write_text(json.dumps(manifest))
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match=error):
        update.rollback(backup)


def test_partial_copy_failure_keeps_receipt_for_explicit_recovery(fixture, monkeypatch):
    baseline, candidate, runtime, backup = fixture
    copy2 = update.shutil.copy2
    target = runtime / "Mods/balatrobot/patch.lua"

    def fail_patch_copy(source, destination, *args, **kwargs):
        if Path(destination).parent == target.parent and Path(destination).name.startswith(".patch.lua."):
            Path(destination).write_bytes(b"partial staged bytes")
            raise OSError("injected copy failure")
        return copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(update.shutil, "copy2", fail_patch_copy)
    with pytest.raises(OSError, match="injected"):
        update.update(candidate, baseline, backup, apply=True)
    assert (backup / update.RECEIPT).is_file()
    monkeypatch.setattr(update.shutil, "copy2", copy2)
    assert update.rollback(backup)["restored"]
    assert (runtime / "bridge.ps1").read_bytes() == b"old bridge"
    assert target.read_bytes() == b"old patch"


def test_source_checksum_drift_after_snapshot_preserves_rollback_data(fixture, monkeypatch):
    baseline, candidate, runtime, backup = fixture
    snapshot = update.snapshot_runtime

    def change_source_after_snapshot(path, *, runtime):
        result = snapshot(path, runtime=runtime)
        (candidate / "native/bridge.ps1").write_bytes(b"changed after plan")
        return result

    monkeypatch.setattr(update, "snapshot_runtime", change_source_after_snapshot)
    with pytest.raises(ValueError, match="RUNTIME_SOURCE_OR_DESTINATION_DRIFT"):
        update.update(candidate, baseline, backup, apply=True)
    assert (backup / update.RECEIPT).is_file()
    assert (runtime / "bridge.ps1").read_bytes() == b"old bridge"
    assert update.rollback(backup)["restored"]
