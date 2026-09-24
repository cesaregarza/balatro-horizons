"""Update only pinned Balatro Horizons instrumentation in an owned runtime.

The caller stops the idle service and holds the native worker lock before use.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from balatro_horizons.cli.install_candidate import digest, snapshot_runtime
from balatro_horizons.evidence.lock import native_path
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.environment import Environment, instrument_hash, verify_files

RECEIPT = "runtime-update.json"


def _linux_path(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("SYMLINK_PATH_REFUSED")
    return native_path(path.resolve(strict=False))


def _configured_runtime():
    value = str(Environment().runtime)
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("NONCANONICAL_RUNTIME_PATH")
    return path


def _runtime_child(runtime, relative):
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("INVALID_RUNTIME_PATH")
    path = runtime / rel
    if any(item.is_symlink() for item in (path, *path.parents) if item == runtime or item.is_relative_to(runtime)):
        raise ValueError("RUNTIME_SYMLINK_REFUSED")
    return path


def _validate_runtime_tree(runtime):
    if any(item.is_symlink() for item in (runtime, *runtime.parents)):
        raise ValueError("RUNTIME_SYMLINK_REFUSED")
    for name in ("Balatro.exe", "version.dll", "bridge.ps1", "environment.lock.json", "horizons-owned.json", "Mods"):
        path = runtime / name
        if path.is_symlink():
            raise ValueError("RUNTIME_SYMLINK_REFUSED")
    mods = runtime / "Mods"
    if not mods.is_dir():
        raise ValueError("MOD_DIRECTORY_REQUIRED")
    for directory, dirs, files in os.walk(mods, followlinks=False):
        for name in dirs + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError("RUNTIME_SYMLINK_REFUSED")


def _stage_copy(source, target, expected):
    descriptor, temporary = tempfile.mkstemp(prefix="." + target.name + ".", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    staged = Path(temporary)
    try:
        shutil.copy2(source, staged)
        if digest(staged) != expected:
            raise ValueError("STAGED_COPY_CHECKSUM_MISMATCH")
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def _json(path):
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("INVALID_RUNTIME_LOCK") from error
    if not isinstance(value, dict):
        raise ValueError("INVALID_RUNTIME_LOCK")
    return value


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _candidate(path):
    root = _linux_path(path)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("NOT_A_WORKTREE_ROOT")
    if git("status", "--porcelain"):
        raise ValueError("WORKTREE_NOT_CLEAN")
    return root, git("rev-parse", "HEAD")


def _plan(candidate, baseline_lock, runtime):
    bridge = _linux_path(candidate / "native/bridge.ps1")
    patches = _linux_path(candidate / "native/patches")
    if not bridge.is_file() or not patches.is_dir():
        raise ValueError("CANDIDATE_INSTRUMENTATION_REQUIRED")
    targets = [(bridge, runtime / "bridge.ps1")]
    for source in sorted(patches.glob("*.lua")):
        source = _linux_path(source)
        if not source.is_file():
            raise ValueError("REGULAR_FILES_REQUIRED")
        targets.append((source, runtime / "Mods/balatrobot" / source.name))
    rows = []
    for source, target in targets:
        target = _runtime_child(runtime, target.relative_to(runtime))
        if not source.is_file() or not target.is_file():
            raise ValueError("EXISTING_REGULAR_TARGETS_REQUIRED")
        before, after = digest(target), digest(source)
        if before != after:
            rows.append({"path": str(target.relative_to(runtime)), "source": str(source),
                         "before_sha256": before, "after_sha256": after})
    if not rows:
        raise ValueError("EMPTY_RUNTIME_UPDATE")
    new_lock = dict(baseline_lock)
    new_lock["bridge_sha256"] = digest(bridge)
    mods = runtime / "Mods"
    hashes = {str(path.relative_to(mods)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(mods.rglob("*"))
              if path.is_file() and "lovely" not in path.relative_to(mods).parts and ".git" not in path.parts}
    for row in rows:
        if row["path"].startswith("Mods/"):
            hashes[row["path"][5:]] = row["after_sha256"]
    new_lock["mods_sha256"] = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return rows, new_lock


def update(candidate, baseline, backup, *, apply=False):
    candidate, revision = _candidate(candidate)
    baseline, backup = _linux_path(baseline), _linux_path(backup)
    runtime = _configured_runtime()
    _validate_runtime_tree(runtime)
    marker = runtime / "horizons-owned.json"
    if not marker.is_file() or _json(marker).get("purpose") != "isolated-balatro-horizons":
        raise ValueError("OWNED_RUNTIME_REQUIRED")
    baseline_lock_path = baseline / "private/environment.lock.json"
    _linux_path(baseline_lock_path)
    baseline_lock = _json(baseline_lock_path)
    runtime_lock_path = runtime / "environment.lock.json"
    if _json(runtime_lock_path) != baseline_lock:
        raise ValueError("RUNTIME_LOCK_BASELINE_MISMATCH")
    try:
        if verify_files(Environment(), baseline_lock_path) != baseline_lock:
            raise ValueError("BASELINE_RUNTIME_DRIFT")
    except NativeFailure as error:
        raise ValueError("BASELINE_RUNTIME_DRIFT") from error
    candidate_lock = candidate / "private/environment.lock.json"
    _linux_path(candidate_lock)
    existed = candidate_lock.exists()
    rows, new_lock = _plan(candidate, baseline_lock, runtime)
    if existed and _json(candidate_lock) not in (baseline_lock, new_lock):
        raise ValueError("CANDIDATE_LOCK_CONFLICT")
    report = {"candidate_commit": revision, "baseline": str(baseline), "runtime": str(runtime),
              "changes": [{k: v for k, v in row.items() if k != "source"} for row in rows], "apply": apply}
    if not apply:
        return report
    snapshot_runtime(backup, runtime=runtime)
    receipt = {"schema": "runtime-update-v1", **report, "prior_lock": baseline_lock,
               "new_lock": new_lock, "candidate_lock_existed": existed}
    _atomic_json(backup / RECEIPT, receipt)
    for row in rows:
        source, target = Path(row["source"]), runtime / row["path"]
        if digest(source) != row["after_sha256"] or digest(target) != row["before_sha256"]:
            raise ValueError("RUNTIME_SOURCE_OR_DESTINATION_DRIFT")
        _stage_copy(source, target, row["after_sha256"])
        if digest(target) != row["after_sha256"]:
            raise ValueError("RUNTIME_COPY_CHECKSUM_MISMATCH")
    if hashlib.sha256((runtime / "bridge.ps1").read_bytes()).hexdigest() != new_lock["bridge_sha256"]:
        raise ValueError("RUNTIME_BRIDGE_MISMATCH")
    if instrument_hash(runtime / "Mods") != new_lock["mods_sha256"]:
        raise ValueError("RUNTIME_MODS_MISMATCH")
    _atomic_json(runtime / "environment.lock.json", new_lock)
    _atomic_json(candidate_lock, new_lock)
    return {**report, "backup": str(backup), "receipt": str(backup / RECEIPT)}


def rollback(backup):
    backup = _linux_path(backup)
    receipt = _json(backup / RECEIPT)
    if receipt.get("schema") != "runtime-update-v1":
        raise ValueError("INVALID_RUNTIME_UPDATE_RECEIPT")
    runtime = _configured_runtime()
    if receipt.get("runtime") != str(runtime):
        raise ValueError("RUNTIME_RECEIPT_MISMATCH")
    _validate_runtime_tree(runtime)
    manifest = _json(backup / "manifest.json")
    changes = receipt.get("changes")
    if not isinstance(receipt.get("new_lock"), dict):
        raise ValueError("INVALID_RUNTIME_UPDATE_RECEIPT")
    def allowed(rel):
        return rel == "bridge.ps1" or (
            rel.startswith("Mods/balatrobot/") and Path(rel).name.endswith(".lua")
            and len(Path(rel).parts) == 3 and Path(rel).name != ".lua"
        )
    if not isinstance(changes, list) or not changes or any(not isinstance(row, dict) for row in changes):
        raise ValueError("INVALID_RUNTIME_UPDATE_RECEIPT")
    paths = [row.get("path") for row in changes]
    if any(not isinstance(rel, str) for rel in paths):
        raise ValueError("INVALID_RUNTIME_UPDATE_PATHS")
    if len(set(paths)) != len(paths) or any(Path(rel).as_posix() != rel or not allowed(rel) for rel in paths):
        raise ValueError("INVALID_RUNTIME_UPDATE_PATHS")
    if any(not isinstance(row.get("after_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["after_sha256"])
           or not isinstance(row.get("before_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["before_sha256"])
           for row in changes):
        raise ValueError("INVALID_RUNTIME_UPDATE_HASHES")
    paths.append("environment.lock.json")
    after_lock = hashlib.sha256((json.dumps(receipt["new_lock"], indent=2, sort_keys=True) + "\n").encode()).hexdigest()
    for rel in paths:
        target, saved = _runtime_child(runtime, rel), _linux_path(backup / "files" / rel)
        after = after_lock if rel == "environment.lock.json" else next(r["after_sha256"] for r in changes if r["path"] == rel)
        before = manifest.get(rel)
        if not isinstance(before, str) or not re.fullmatch(r"[0-9a-f]{64}", before):
            raise ValueError("BACKUP_CHECKSUM_MISSING: " + rel)
        if digest(target) not in {after, before}:
            raise ValueError("POST_INSTALL_DRIFT: " + rel)
        if digest(saved) != before:
            raise ValueError("BACKUP_CHECKSUM_MISMATCH: " + rel)
    for rel in paths[:-1]:
        _stage_copy(backup / "files" / rel, runtime / rel, manifest[rel])
        if digest(runtime / rel) != manifest[rel]:
            raise ValueError("ROLLBACK_CHECKSUM_MISMATCH: " + rel)
    rel = paths[-1]
    _stage_copy(backup / "files" / rel, runtime / rel, manifest[rel])
    if digest(runtime / rel) != manifest[rel]:
        raise ValueError("ROLLBACK_CHECKSUM_MISMATCH: " + rel)
    verify_files(Environment(), runtime / "environment.lock.json")
    return {"restored": True, "runtime": str(runtime)}


def configure_parser(parser: argparse.ArgumentParser):
    parser.add_argument("--root", type=Path, required=True, help="Clean candidate Linux checkout")
    parser.add_argument("--baseline", type=Path, required=True, help="Prior Linux release checkout")
    parser.add_argument("--backup", type=Path, required=True, help="New Linux snapshot directory")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="Apply the reviewed dry-run plan")
    modes.add_argument("--rollback", action="store_true", help="Restore a partial or complete update")
    parser.set_defaults(operation_handler=run, operation_parser=parser)


def run(args):
    result = rollback(args.backup) if args.rollback else update(args.root, args.baseline, args.backup, apply=args.apply)
    print(json.dumps(result, sort_keys=True))
