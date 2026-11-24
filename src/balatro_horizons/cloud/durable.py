"""Ledger writes that survive power loss, not only a process crash.

A rename is only durable once the directory holding it is synced, and a new
directory only once its parent is synced. The Pi can lose power right after the
provider accepts a create, so the intent record must already be on disk.
"""

import json
import os
import uuid
from pathlib import Path


def durable_json(path: Path, value):
    ensure_directory(path.parent)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf8") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def ensure_directory(directory: Path):
    if directory.is_dir():
        return
    ensure_directory(directory.parent)
    directory.mkdir(exist_ok=True)
    sync_directory(directory.parent)


def sync_directory(directory: Path):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
