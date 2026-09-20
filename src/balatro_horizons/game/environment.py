"""Validated native-runtime configuration and environment integrity gates.

This module deliberately does not import :mod:`balatro_horizons.config`.  The
configuration module re-exports :class:`Environment` during the game-boundary
cutover, so importing it here would create a circular dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from balatro_horizons.game.contract import NativeFailure

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LAUNCH_HANDSHAKE_TIMEOUT_SECONDS = 45
DEFAULT_HTTP_TIMEOUT_SECONDS = 90
_WSL_RUNTIME = re.compile(r"^/mnt/(?P<drive>[A-Za-z])(?:/(?P<path>.*))?$")


def windows_runtime_path(runtime: str) -> str:
    """Convert a WSL ``/mnt/<drive>/...`` path to a PowerShell path.

    This is intentionally a pure string operation.  In particular, it never
    probes or resolves the input path, which keeps offline checks from touching
    Windows-mounted filesystems.
    """

    value = str(runtime)
    match = _WSL_RUNTIME.fullmatch(value)
    if not match:
        return value
    suffix = (match.group("path") or "").replace("/", "\\")
    drive = match.group("drive").upper()
    return f"{drive}:\\{suffix}" if suffix else f"{drive}:\\"


class Environment(BaseModel):
    """Native runtime settings shared by config, transport, and sessions."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    adapter: str = "balatrobot"
    deck: str = "RED"
    stake: str = "GOLD"
    unlock_profile: str = "dedicated_fully_unlocked"
    resolved_manifest: str = "private/environment.lock.json"
    require_live_certification: Literal[True] = True
    runtime: str = "/mnt/d/BalatroHorizonsRuntime"
    powershell: str = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    port: int = Field(default=12346, ge=1024, le=65535)

    # ``timeout_seconds`` remains for configuration compatibility.  New code
    # uses the named values so handshake and HTTP budgets cannot be confused.
    timeout_seconds: int = Field(default=90, ge=1, le=300)
    launch_timeout_seconds: int = Field(
        default=DEFAULT_LAUNCH_HANDSHAKE_TIMEOUT_SECONDS, ge=1, le=300
    )
    http_timeout_seconds: int = Field(default=DEFAULT_HTTP_TIMEOUT_SECONDS, ge=1, le=300)

    @property
    def windows_runtime(self) -> str:
        return windows_runtime_path(self.runtime)

    @property
    def launch_handshake_timeout_seconds(self) -> int:
        """Descriptive alias used by startup/handshake callers."""

        return self.launch_timeout_seconds

    @property
    def handshake_timeout_seconds(self) -> int:
        return self.launch_timeout_seconds


def instrument_hash(root: str | Path) -> str:
    """Hash instrument files while excluding generated loader metadata."""

    root = Path(root)
    hashes = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and "lovely" not in path.relative_to(root).parts
        and ".git" not in path.parts
    }
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def _manifest_path(environment: Environment, manifest: str | Path | None) -> Path:
    value = Path(manifest or environment.resolved_manifest)
    return value if value.is_absolute() else ROOT / value


def verify_files(environment: Environment, manifest: str | Path | None = None) -> dict:
    """Verify the pinned runtime files and instrument hash."""

    path = _manifest_path(environment, manifest)
    if not path.is_file():
        raise NativeFailure("NATIVE_RUNTIME_NOT_INSTALLED")
    lock = json.loads(path.read_text())
    root = Path(environment.runtime)
    for name, key in (
        ("Balatro.exe", "game_sha256"),
        ("version.dll", "injector_sha256"),
        ("bridge.ps1", "bridge_sha256"),
    ):
        file = root / name
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != lock[key]:
            raise NativeFailure("ENVIRONMENT_FILE_MISMATCH")
    if instrument_hash(root / "Mods") != lock["mods_sha256"]:
        raise NativeFailure("MOD_ENVIRONMENT_MISMATCH")
    return lock


def verify_identity(
    state: dict,
    *,
    instance_id: str | None = None,
    lock: dict | None = None,
    calibration: bool = False,
) -> None:
    """Verify process identity, save isolation, and runtime policy."""

    bh = state.get("bh", {})
    if instance_id and bh.get("instance_id") != instance_id:
        raise NativeFailure("NATIVE_PROCESS_IDENTITY_MISMATCH")
    if lock is not None and bh.get("loaded_manifest") != lock:
        raise NativeFailure("LOADED_ENVIRONMENT_MISMATCH")
    if (
        bh.get("identity") != "BalatroHorizons"
        or Path(bh.get("save_directory", "").replace("\\", "/")).name != "BalatroHorizons"
    ):
        raise NativeFailure("SAVE_ISOLATION_FAILED")
    if bh.get("calibration") is not calibration:
        raise NativeFailure("RUNTIME_CALIBRATION_POLICY_MISMATCH")
    if bh.get("profile_policy") != "fully_unlocked_v1" or bh.get("headless") or bh.get("fast"):
        raise NativeFailure("RUNTIME_POLICY_MISMATCH")
