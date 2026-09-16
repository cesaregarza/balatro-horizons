#!/usr/bin/env python3
"""Preview or apply Windows launch context to the existing local workbench service.

Run from a working WSL shell. --apply restarts only balatro-horizons.service.
Credentials and unrelated shell variables are never copied.
"""

import argparse
import fcntl
import os
import subprocess
from pathlib import Path

from balatro_horizons.config import ROOT

UNIT = "balatro-horizons.service"
WINDOWS_VARIABLES = {
    "SYSTEMROOT": "p",
    "SYSTEMDRIVE": "",
    "USERPROFILE": "p",
    "APPDATA": "p",
    "LOCALAPPDATA": "p",
    "PROGRAMDATA": "p",
    "COMSPEC": "p",
    "USERNAME": "",
    "USERDOMAIN": "",
}


def session_environment(source):
    required = ("WSL_INTEROP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    if any(not source.get(key) for key in required):
        raise ValueError("RUN_FROM_A_WINDOWS_CONNECTED_WSL_SHELL")
    result = {key: source[key] for key in WINDOWS_VARIABLES if source.get(key)}
    result["WSLENV"] = ":".join(
        key + ("/" + flag if flag else "")
        for key, flag in WINDOWS_VARIABLES.items()
        if key in result
    )
    for key in ("WSL_INTEROP", "WSL_DISTRO_NAME", "WSL2_GUI_APPS_ENABLED"):
        if source.get(key):
            result[key] = source[key]
    return result


def dropin(environment):
    lines = ["[Service]"]
    for key, value in sorted(environment.items()):
        if any(character in value for character in "\n\r\x00"):
            raise ValueError("INVALID_SESSION_ENVIRONMENT")
        escaped = (key + "=" + value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        lines.append(f'Environment="{escaped}"')
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    environment = session_environment(os.environ)
    content = dropin(environment)
    print("Launch variables: " + ", ".join(sorted(environment)), flush=True)
    if not args.apply:
        print("Preview only; use --apply to configure and restart the idle workbench.")
        return
    runtime = Path(f"/run/user/{os.getuid()}")
    destination = runtime / "systemd/user" / (UNIT + ".d") / "30-wsl-session.conf"
    with (ROOT / "private/native-worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("NATIVE_WORKER_BUSY") from None
        subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT], check=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
        destination.chmod(0o600)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)
        subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT], check=True)
    print("Applied session context to the workbench service; credentials and budgets unchanged.")


if __name__ == "__main__":
    main()
