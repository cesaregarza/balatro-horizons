#!/usr/bin/env python3
"""Register or check the Windows connection without restarting the backend.

From a Windows-connected WSL terminal:
  .venv/bin/python scripts/configure_workbench_session.py --apply

No game launch, paid call, credential copy, or service restart occurs.
"""

import argparse
import fcntl
import json
import os

from balatro_horizons.config import ROOT
from balatro_horizons.engine.windows_context import (
    connection_status,
    register_session,
    require_socket,
    session_environment,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Register this shell's connection while idle")
    mode.add_argument("--check", action="store_true", help="Check registered connection; no private values")
    args = parser.parse_args()
    if args.check:
        result = connection_status()
        print(json.dumps(result, sort_keys=True))
        return int(not result["ready"])
    try:
        environment = session_environment(os.environ)
        require_socket(environment)
        print("Launch variables: " + ", ".join(sorted(environment)), flush=True)
        if not args.apply:
            print("Preview only; --apply registers this connection without restarting the backend.")
            return 0
        lock_path = ROOT / "private/native-worker.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("NATIVE_WORKER_BUSY") from None
            register_session(os.environ)
        print("Windows connection registered. Backend, game, credentials and budgets unchanged.")
        return 0
    except (ValueError, OSError) as error:
        code = str(error) if str(error).isupper() else "WINDOWS_SESSION_REGISTRATION_FAILED"
        parser.exit(1, code + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
