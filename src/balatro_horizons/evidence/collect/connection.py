"""One owned launch and registration/RPC recovery, never capability certification."""

import fcntl
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.provenance import (
    implementation_fingerprint,
    native_implementation_fingerprint,
)
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.transport import WindowsBridge
from balatro_horizons.game.windows_context import (
    bridge_environment,
    connection_status,
    context_path,
    load_session,
    register_session,
)
from balatro_horizons.storage.journal import atomic_json, digest, now


def source_identity():
    if subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"]).strip():
        raise ValueError("CONNECTION_CHECK_REQUIRES_CLEAN_SOURCE")
    return {
        "commit": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "implementation_hash": implementation_fingerprint(),
        "native_implementation_hash": native_implementation_fingerprint(),
    }


def runtime_state(environment):
    """Read only owned-runtime process count and whether its port is occupied."""
    runtime = environment.windows_runtime.replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop'; "
        f"$exe='{runtime}\\Balatro.exe'; "
        "$owned=@(Get-Process -Name Balatro -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path -eq $exe }); "
        "$listeners=[System.Net.NetworkInformation.IPGlobalProperties]::"
        "GetIPGlobalProperties().GetActiveTcpListeners(); "
        f"$listening=@($listeners | Where-Object {{ $_.Port -eq {environment.port} }}).Count -gt 0; "
        "@{owned_processes=$owned.Count; port_listening=$listening} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [environment.powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        env=bridge_environment(), capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError("CONNECTION_PROCESS_CHECK_FAILED")
    try:
        value = json.loads(result.stdout.lstrip("\ufeff"))
        if type(value["owned_processes"]) is not int or type(value["port_listening"]) is not bool:
            raise ValueError
        return {key: value[key] for key in ("owned_processes", "port_listening")}
    except (ValueError, KeyError, TypeError):
        raise ValueError("CONNECTION_PROCESS_CHECK_INVALID") from None


def require_idle(environment):
    state = runtime_state(environment)
    if state["owned_processes"] or state["port_listening"]:
        raise ValueError("NATIVE_RUNTIME_BUSY")
    return state


def require_source_instrumentation(bridge):
    pairs = [(ROOT / "native/bridge.ps1", bridge.root / "bridge.ps1")]
    pairs.extend((path, bridge.root / "Mods/balatrobot" / path.name)
                 for path in (ROOT / "native/patches").glob("*.lua"))
    pairs.extend((path, bridge.root / "Mods/HorizonsIsolation" / path.relative_to(ROOT / "native/isolation"))
                 for path in (ROOT / "native/isolation").rglob("*") if path.is_file())
    for source, installed in pairs:
        if source.read_bytes() != installed.read_bytes():
            raise ValueError("CONNECTION_INSTRUMENTATION_SOURCE_MISMATCH")


def wait_ready(bridge, state):
    deadline = time.monotonic() + bridge.env.launch_timeout_seconds
    while True:
        bridge.verify_identity(state)
        if state["bh"]["ready"] and not state["bh"]["busy"]:
            return
        if time.monotonic() >= deadline:
            raise ValueError("CONNECTION_READINESS_TIMEOUT")
        time.sleep(0.1)
        state = bridge.rpc("bh_inspect")


def recover_rpc(bridge):
    """Temporarily unregister only this checkout; always restore before cleanup."""
    bridge._close_rpc()
    path = context_path()
    saved = path.with_name("windows-session-connection-check-" + uuid.uuid4().hex + ".json")
    path.rename(saved)
    try:
        if connection_status()["code"] != "WINDOWS_SESSION_NOT_CONFIGURED":
            raise ValueError("CONNECTION_UNREGISTERED_STATUS_MISMATCH")
        try:
            bridge.rpc("bh_inspect")
        except NativeFailure as error:
            if str(error) != "WINDOWS_SESSION_NOT_CONFIGURED":
                raise
        else:
            raise ValueError("CONNECTION_UNREGISTERED_RPC_NOT_REFUSED")
    finally:
        saved.replace(path)
    # Re-register the same validated socket, not an inherited provider environment.
    # This tests refresh and a fresh RPC subprocess, not actual socket destruction.
    register_session(load_session())
    state = bridge.rpc("bh_inspect")
    wait_ready(bridge, state)
    return {"unregistered_rpc_refused": True, "registration_refreshed": True,
            "rpc_reopened_same_game": True, "actual_socket_expiry_tested": False}


def error_code(error):
    code = str(error)
    return code if code.isupper() and len(code) < 100 else type(error).__name__


def run_owned(bridge, result):
    """Never stop a pre-existing process; cleanup uses only our launch nonce."""
    result["idle_before"] = require_idle(bridge.env)
    result["launch_attempts"] = 1
    try:
        raw = bridge.launch()
        result["native_launches"] = 1
        wait_ready(bridge, raw)
        result["startup_identity_verified"] = True
        result.update(recover_rpc(bridge))
    finally:
        try:
            bridge._close_rpc()
        finally:
            if bridge.instance_id is not None:
                bridge.stop()
            result["idle_after"] = require_idle(bridge.env)
            result["owned_process_stopped"] = True


def collect(environment, report):
    """Write one immutable, sanitized receipt, including a failed check; no retry."""
    report = Path(report)
    if report.is_relative_to("/mnt"):
        raise ValueError("CONNECTION_REPORT_REQUIRES_NEW_LINUX_PATH")
    report = report.resolve()
    if report.is_relative_to("/mnt") or report.exists():
        raise ValueError("CONNECTION_REPORT_REQUIRES_NEW_LINUX_PATH")
    result = {
        "schema_version": 1, "scope": "native_connection_only", "status": "failed",
        "created_at": now(), "native_launches": 0, "launch_attempts": 0, "game_resets": 0,
        "provider_calls": 0, "cost_usd": 0, "evaluation_eligible": False,
        "capability_certificates_created": False, "restoration_certification_included": False,
    }
    try:
        result["source"] = source_identity()
        load_session()
        bridge = WindowsBridge(environment)
        bridge.calibration = True
        lock = bridge.verify_files()
        result["environment_hash"] = digest(lock)
        result["bridge_sha256"] = hashlib.sha256((ROOT / "native/bridge.ps1").read_bytes()).hexdigest()
        require_source_instrumentation(bridge)
        with (ROOT / "private/native-worker.lock").open("a") as worker:
            try:
                fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("NATIVE_WORKER_BUSY") from None
            load_session()
            run_owned(bridge, result)
        if source_identity() != result["source"] or digest(bridge.verify_files()) != result["environment_hash"]:
            raise ValueError("CONNECTION_SOURCE_OR_ENVIRONMENT_CHANGED")
        result["status"] = "passed"
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        result["reason"] = error_code(error)
    atomic_json(report, result, immutable=True)
    report.chmod(0o600)
    return result
