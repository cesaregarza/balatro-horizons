"""Read the pinned native environment manifest through one small seam."""

import json
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.storage.journal import digest

LOCK_RELATIVE_PATH = Path("private/environment.lock.json")


def native_path(path: Path) -> Path:
    """Resolve a Linux workbench path while refusing mounted filesystems."""
    resolved = Path(path).resolve()
    if resolved.is_relative_to("/mnt"):
        raise ValueError("MOUNTED_PATH_OUTSIDE_SCOPE")
    return resolved


def lock_path(root: Path | None = None) -> Path:
    """Return the private lock path for a native Linux checkout."""
    return native_path(ROOT if root is None else root) / LOCK_RELATIVE_PATH


def read_lock(root: Path | None = None) -> dict:
    """Read and validate the JSON lock without touching any runtime files."""
    path = lock_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except FileNotFoundError as error:
        raise ValueError("NATIVE_ENVIRONMENT_LOCK_MISSING") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("NATIVE_ENVIRONMENT_LOCK_INVALID") from error
    if not isinstance(value, dict):
        raise ValueError("NATIVE_ENVIRONMENT_LOCK_INVALID")
    return value


def lock_digest(root: Path | None = None) -> str:
    """Return the canonical digest used by certificates and reports."""
    return digest(read_lock(root))


def read_with_digest(root: Path | None = None) -> tuple[dict, str]:
    """Read the manifest once and return it with its canonical digest."""
    value = read_lock(root)
    return value, digest(value)
