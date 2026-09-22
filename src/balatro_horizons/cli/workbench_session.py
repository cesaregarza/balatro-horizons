#!/usr/bin/env python3
"""Preview, register, or check the Windows launch context.

Run from a Windows-connected WSL shell. Registration is read by future bridge
processes; it never restarts the backend, writes a service drop-in, launches a
game, or copies credentials.
"""

import fcntl
import json
import os

from balatro_horizons.config import ROOT
from balatro_horizons.game.windows_context import (
    SESSION_ERROR_CODES,
    connection_status,
    register_session,
    require_socket,
    session_environment,
)


def configure_parser(parser):
    parser.description = __doc__
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Register this shell's connection")
    mode.add_argument("--check", action="store_true", help="Check the registered connection")
    parser.set_defaults(operation_handler=run, operation_parser=parser)


def run(args):
    if args.check:
        result = connection_status()
        print(json.dumps(result, sort_keys=True))
        return int(not result["ready"])
    try:
        environment = session_environment(os.environ)
        require_socket(environment)
        print("Launch variables: " + ", ".join(sorted(environment)), flush=True)
        if not args.apply:
            print(
                "Preview only; use --apply to register this connection without "
                "restarting the backend."
            )
            return 0
        lock_path = ROOT / "private/native-worker.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("NATIVE_WORKER_BUSY") from None
            register_session(os.environ)
    except (OSError, ValueError) as error:
        code = str(error)
        if code not in SESSION_ERROR_CODES and code != "NATIVE_WORKER_BUSY":
            code = "WINDOWS_SESSION_REGISTRATION_FAILED"
        raise SystemExit(code) from None
    print(
        "Windows connection registered; backend, game, credentials and budgets unchanged."
    )
