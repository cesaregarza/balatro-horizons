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
    parser.add_argument("--preset", choices=("smoke", "pilot"), default="pilot")
    args = parser.parse_args()
    if args.workbench:
        return workbench_startup(args.preset)
    bridge = WindowsBridge(load_config(args.config).environment)
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
