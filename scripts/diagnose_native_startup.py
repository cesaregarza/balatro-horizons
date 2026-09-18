#!/usr/bin/env python3
"""Probe the isolated native runtime; --restart checks a fresh handshake then stops it.

Requires task-scoped access to the configured Windows runtime and PowerShell.
Never calls a model, changes pinned files, or resets a run journal.
"""

import argparse
import fcntl
import json
import time
from datetime import datetime
from pathlib import Path

from balatro_horizons.config import ROOT, load_config
from balatro_horizons.engine.certification import require_environment_certificate
from balatro_horizons.engine.native import NativeFailure, NativeRejected, WindowsBridge


def executable_access(environment):
    """Read only the configured executable header; no processes or RPC calls."""
    try:
        with Path(environment.powershell).open("rb") as stream:
            valid = stream.read(2) == b"MZ"
        return {"status": "passed" if valid else "failed", "provider_calls": 0,
                "game_launches": 0,
                "reason": None if valid else "WINDOWS_EXECUTABLE_HEADER_INVALID"}
    except OSError as error:
        return {"status": "failed", "reason": f"WINDOWS_EXECUTABLE_READ_ERROR_{error.errno}",
                "provider_calls": 0, "game_launches": 0}


def launch_metadata(root):
    """Return only allowlisted metadata; never emit raw logs or process nonces."""
    path = root / "process.json"
    if not path.exists():
        return {"process_record_present": False}
    record = json.loads(path.read_text(encoding="utf-8-sig"))
    started = datetime.fromisoformat(record["started"].replace("Z", "+00:00")).timestamp()
    logs = list((root / "Mods/lovely/log").glob("*.log"))
    latest = max((path.stat().st_mtime for path in logs), default=None)
    return {
        "process_record_present": True,
        "started": record["started"],
        "lovely_log_after_launch": latest is not None and latest >= started,
        "log_sizes": {
            name: (root / name).stat().st_size if (root / name).exists() else None
            for name in ("stdout.log", "stderr.log")
        },
    }


def probe(bridge):
    result = {"launch": launch_metadata(bridge.root)}
    try:
        raw = bridge.rpc("bh_inspect")
        bridge.verify_identity(raw)
        result.update(status="passed", phase=raw.get("state"), ready=raw["bh"].get("ready"))
    except (NativeFailure, NativeRejected) as error:
        result.update(status="failed", reason=str(error))
    finally:
        bridge._close_rpc()
    return result


def progress(bridge):
    """Inspect an owned running game without acquiring its worker or changing it."""
    try:
        bridge.verify_files()
        record = json.loads((bridge.root / "process.json").read_text(encoding="utf-8-sig"))
        bridge.instance_id = record["instance_id"]
        raw = bridge.rpc("bh_inspect")
        bridge.verify_identity(raw)
        return {"status": "passed", "started": record["started"],
                "phase": raw.get("state"), "ante": raw.get("ante_num"),
                "round_number": raw.get("round_num"), "ready": raw["bh"].get("ready"),
                "busy": raw["bh"].get("busy"), "provider_calls": 0, "game_launches": 0}
    finally:
        bridge._close_rpc()


def restart(bridge):
    """Stop only the registered runtime, test a certified launch, clean our own process."""
    lock = bridge.verify_files()
    require_environment_certificate(lock, bridge.env)
    record_path = bridge.root / "process.json"
    if record_path.exists():
        bridge.instance_id = json.loads(record_path.read_text(encoding="utf-8-sig"))["instance_id"]
        bridge.stop()
    # launch() assigns a fresh nonce before requesting process creation. Its stop
    # checks nonce, executable path and start time, including after a failed launch.
    try:
        raw = bridge.launch()
        return {"status": "passed", "phase": raw.get("state"), "identity_verified": True}
    finally:
        bridge.stop()


def workbench_startup(preset):
    """Check the actual browser worker without a provider or a committed move."""
    from balatro_horizons.operator_client import operator_request
    from balatro_horizons.storage.journal import Store, atomic_json, now

    store = Store(ROOT / "data")
    started = operator_request(
        "/runs",
        method="POST",
        payload={"agent": "human", "offline": False, "calibration": False, "preset": preset},
    )
    eid = started["episode_id"]
    print(json.dumps({"progress": "workbench_starting", "episode_id": eid}), flush=True)
    result = {"episode_id": eid, "path": "live_workbench_api", "created_at": now()}
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            summary = store.summary(eid)
            if summary:
                raise NativeFailure(summary["reason"])
            response = operator_request("/operator/human")
            observation = response.get("context", {}).get("observation", {})
            if response.get("waiting") and observation.get("episode_id") == eid:
                result.update(status="passed", native_ready=True, phase=observation["phase"])
                break
            time.sleep(1)
        else:
            raise NativeFailure("WORKBENCH_STARTUP_PROBE_TIMEOUT")
    except NativeFailure as error:
        result.update(status="failed", reason=str(error))
    finally:
        if store.summary(eid) is None:
            operator_request("/stop", method="POST")
        deadline = time.monotonic() + 20
        while store.summary(eid) is None and time.monotonic() < deadline:
            time.sleep(0.5)
    summary = store.summary(eid)
    if summary is None:
        raise NativeFailure("DIAGNOSTIC_STOP_TIMEOUT")
    assert summary["provider_calls"] == 0 and summary["cost_usd"] == 0
    assert not store.manifest(eid)["evaluation_eligible"]
    result.update(summary=summary, provider_calls=0, cost_usd=0, evaluation_eligible=False)
    atomic_json(ROOT / "reports/verification/startup-recovery.json", result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/pilot.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restart", action="store_true", help="Restart only an idle runtime")
    mode.add_argument("--workbench", action="store_true", help="Test the live browser worker")
    mode.add_argument("--progress", action="store_true", help="Read-only public progress of the owned game, including during verification")
    parser.add_argument("--calibration", action="store_true", help="With --progress, expect an unpaid verification process")
    mode.add_argument("--executable-access", action="store_true",
                      help="Read the configured PowerShell header only; no Windows process or game launch")
    parser.add_argument("--preset", choices=("smoke", "pilot"), default="pilot")
    args = parser.parse_args()
    if args.calibration and not args.progress:
        parser.error("--calibration requires --progress")
    if args.executable_access:
        result = executable_access(load_config(args.config).environment)
        print(json.dumps(result, sort_keys=True))
        return int(result["status"] != "passed")
    if args.workbench:
        return workbench_startup(args.preset)
    bridge = WindowsBridge(load_config(args.config).environment)
    if args.progress:
        bridge.calibration = args.calibration
        try:
            print(json.dumps(progress(bridge), sort_keys=True), flush=True)
            return 0
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            print(json.dumps({"status": "failed", "reason": str(error) if str(error).isupper() else type(error).__name__}))
            return 1
    result = {"provider_calls": 0, "restart_requested": args.restart}
    try:
        with (ROOT / "private/native-worker.lock").open("a") as worker_lock:
            try:
                fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise NativeFailure("NATIVE_WORKER_BUSY") from None
            bridge.verify_files()
            result["before"] = probe(bridge)
            print(json.dumps({"progress": "probe_complete", **result}, sort_keys=True), flush=True)
            if args.restart:
                result["restart"] = restart(bridge)
            result["status"] = result.get("restart", result["before"])["status"]
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        message = str(error)
        result.update(
            status="failed",
            reason=message if message.isupper() and len(message) < 100 else type(error).__name__,
        )
    finally:
        bridge._close_rpc()
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
