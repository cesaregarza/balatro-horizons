"""Explicit, replaceable Windows connection for new bridge processes.

The interactive shell environment is ignored until an operator explicitly
registers it. Readers load that registration before each new bridge process,
so reconnecting neither restarts the backend nor replays actions.
"""

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path

from balatro_horizons.config import ROOT

WINDOWS_VARIABLES = {
    "SYSTEMROOT": "p", "SYSTEMDRIVE": "", "USERPROFILE": "p", "APPDATA": "p",
    "LOCALAPPDATA": "p", "PROGRAMDATA": "p", "COMSPEC": "p",
    "USERNAME": "", "USERDOMAIN": "",
}
SESSION_VARIABLES = ("WSL_INTEROP", "WSL_DISTRO_NAME", "WSL2_GUI_APPS_ENABLED")
SESSION_ERROR_CODES = frozenset(
    {
        "WINDOWS_SESSION_NOT_CONFIGURED",
        "WINDOWS_SESSION_EXPIRED",
        "WINDOWS_SESSION_REGISTRATION_INVALID",
        "INVALID_WINDOWS_INTEROP_SOCKET",
        "INVALID_SESSION_ENVIRONMENT",
    }
)
RECONNECT = "Run 'bh review session --apply' from a Windows-connected WSL terminal."


def context_path():
    return ROOT / "private/windows-session.json"


def session_environment(source):
    if not isinstance(source, Mapping):
        raise ValueError("INVALID_SESSION_ENVIRONMENT")
    required = ("WSL_INTEROP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    if any(not source.get(key) for key in required):
        raise ValueError("WINDOWS_SESSION_NOT_CONFIGURED")
    result = {
        key: source[key]
        for key in (*WINDOWS_VARIABLES, *SESSION_VARIABLES)
        if source.get(key)
    }
    if any(
        not isinstance(value, str) or any(c in value for c in "\n\r\x00")
        for value in result.values()
    ):
        raise ValueError("INVALID_SESSION_ENVIRONMENT")
    # WSL's interop sockets live here. Never follow a registration to arbitrary paths.
    socket = Path(result["WSL_INTEROP"])
    if socket.parent != Path("/run/WSL") or not re.fullmatch(r"\d+_interop", socket.name):
        raise ValueError("INVALID_WINDOWS_INTEROP_SOCKET")
    result["WSLENV"] = ":".join(
        key + ("/" + flag if flag else "")
        for key, flag in WINDOWS_VARIABLES.items()
        if key in result
    )
    return result


def require_socket(environment):
    path = Path(environment["WSL_INTEROP"])
    try:
        info = path.lstat()
    except OSError:
        raise ValueError("WINDOWS_SESSION_EXPIRED") from None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("WINDOWS_SESSION_EXPIRED")


def load_session():
    path = context_path()
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("WINDOWS_SESSION_REGISTRATION_INVALID")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("WINDOWS_SESSION_REGISTRATION_INVALID")
        environment = session_environment(value["environment"])
    except FileNotFoundError:
        raise ValueError("WINDOWS_SESSION_NOT_CONFIGURED") from None
    except (OSError, UnicodeError, KeyError, TypeError, ValueError):
        raise ValueError("WINDOWS_SESSION_REGISTRATION_INVALID") from None
    require_socket(environment)
    return environment


def bridge_environment():
    # Provider credentials, proxies and arbitrary WSLENV entries never enter the bridge.
    safe = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    return {**safe, **load_session()}


def register_session(source):
    environment = session_environment(source)
    require_socket(environment)
    path = context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix="windows-session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "environment": environment}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def connection_status():
    try:
        load_session()
        return {
            "ready": True,
            "code": None,
            "message": "Windows connection registered; game startup is verified when a run starts.",
        }
    except ValueError as error:
        return {
            "ready": False,
            "code": str(error),
            "message": "Windows runtime connection needs refreshing. " + RECONNECT,
        }
