"""Socket and process identity proof consumed by disposable_session."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from balatro_horizons.game import windows_context

MAX_ANCESTORS = 16


_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,98}$")


_SOCKET = re.compile(r"^/run/WSL/([0-9]+)_interop$")


def _error_code(error, fallback="DISPOSABLE_SESSION_FAILED"):
    value = str(error)
    return value if _CODE.fullmatch(value) else fallback


def _socket_path(environment):
    value = environment.get("WSL_INTEROP") if isinstance(environment, dict) else None
    if not isinstance(value, str) or len(value) > 4096 or not _SOCKET.fullmatch(value):
        raise ValueError("DISPOSABLE_SOCKET_INVALID")
    return value


def _socket_stat(environment):
    path_value = _socket_path(environment)
    try:
        windows_context.require_socket(environment)
        info = Path(path_value).lstat()
    except ValueError as error:
        raise ValueError(_error_code(error, "DISPOSABLE_SOCKET_INVALID")) from None
    except (OSError, TypeError):
        raise ValueError("DISPOSABLE_SOCKET_EXPIRED") from None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("DISPOSABLE_SOCKET_INVALID")
    return {
        "path": path_value,
        "inode": int(info.st_ino),
        "uid": int(info.st_uid),
        "_dev": int(info.st_dev),
        "_pid": int(_SOCKET.fullmatch(path_value)[1]),
    }


def socket_identity(environment: dict) -> dict:
    value = _socket_stat(environment)
    return {key: value[key] for key in ("path", "inode", "uid")}


def _proc_stat(pid):
    if type(pid) is not int or pid <= 0:
        raise ValueError("DISPOSABLE_ANCESTRY_INVALID")
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        close = raw.rfind(")")
        fields = raw[close + 2 :].split()
        return {
            "pid": int(raw[: raw.find(" ")]),
            "ppid": int(fields[1]),
            "start_ticks": int(fields[19]),
        }
    except (OSError, UnicodeError, IndexError, ValueError):
        raise ValueError("DISPOSABLE_ANCESTRY_INVALID") from None


def _checked_pid(value):
    if type(value) is not int or value <= 0:
        raise ValueError("DISPOSABLE_ANCESTRY_INVALID")
    return value


def _child_ancestors():
    result = []
    pid = os.getpid()
    for _ in range(MAX_ANCESTORS):
        value = _proc_stat(pid)
        result.append(value)
        if value["ppid"] <= 1 or value["ppid"] == pid:
            break
        pid = value["ppid"]
    return result


def _validate_ancestors(receipt, relay_pid):
    chain = receipt.get("ancestors")
    child_pid = _checked_pid(receipt.get("pid"))
    if not isinstance(chain, list) or not chain or len(chain) > MAX_ANCESTORS:
        raise ValueError("DISPOSABLE_ANCESTRY_INVALID")
    parsed = []
    seen = set()
    for item in chain:
        if not isinstance(item, dict):
            raise ValueError("DISPOSABLE_ANCESTRY_INVALID")
        pid = _checked_pid(item.get("pid"))
        ppid = item.get("ppid")
        ticks = item.get("start_ticks")
        if pid in seen or type(ppid) is not int or ppid < 0 or type(ticks) is not int or ticks < 0:
            raise ValueError("DISPOSABLE_ANCESTRY_INVALID")
        seen.add(pid)
        parsed.append({"pid": pid, "ppid": ppid, "start_ticks": ticks})
    if parsed[0]["pid"] != child_pid or receipt.get("ppid") != parsed[0]["ppid"]:
        raise ValueError("DISPOSABLE_ANCESTRY_INVALID")
    for index, item in enumerate(parsed):
        if _proc_stat(item["pid"]) != item:
            raise ValueError("DISPOSABLE_ANCESTRY_INVALID")
        if index + 1 < len(parsed) and item["ppid"] != parsed[index + 1]["pid"]:
            raise ValueError("DISPOSABLE_ANCESTRY_INVALID")
    relay = next((item for item in parsed if item["pid"] == relay_pid), None)
    if relay is None or relay_pid == child_pid:
        raise ValueError("DISPOSABLE_SOCKET_ANCESTRY_INVALID")
    return child_pid, relay["start_ticks"]


def _validate_receipt(receipt, nonce, operator_uid, distro=None):
    if receipt.get("nonce") != nonce:
        raise ValueError("DISPOSABLE_NONCE_INVALID")
    if distro is not None and receipt.get("distro") != distro:
        raise ValueError("DISPOSABLE_DISTRO_INVALID")
    if type(receipt.get("uid")) is not int or receipt["uid"] != operator_uid:
        raise ValueError("DISPOSABLE_UID_INVALID")
    socket = receipt.get("socket")
    if not isinstance(socket, dict) or not isinstance(socket.get("path"), str):
        raise ValueError("DISPOSABLE_SOCKET_INVALID")
    path = socket["path"]
    if len(path) > 4096 or not _SOCKET.fullmatch(path):
        raise ValueError("DISPOSABLE_SOCKET_INVALID")
    if type(socket.get("inode")) is not int or socket["inode"] <= 0:
        raise ValueError("DISPOSABLE_SOCKET_INVALID")
    if type(socket.get("uid")) is not int or socket["uid"] < 0:
        raise ValueError("DISPOSABLE_SOCKET_INVALID")
    if socket["uid"] != operator_uid:
        raise ValueError("DISPOSABLE_UID_INVALID")
    if "dev" in socket and (type(socket["dev"]) is not int or socket["dev"] < 0):
        raise ValueError("DISPOSABLE_SOCKET_INVALID")
    relay_pid = int(_SOCKET.fullmatch(path)[1])
    child_pid, relay_ticks = _validate_ancestors(receipt, relay_pid)
    return {
        "socket": {key: socket[key] for key in ("path", "inode", "uid")},
        "dev": socket.get("dev"),
        "child_pid": child_pid,
        "relay_pid": relay_pid,
        "child_start_ticks": next(
            item["start_ticks"] for item in receipt["ancestors"] if item["pid"] == child_pid
        ),
        "relay_start_ticks": relay_ticks,
    }


def _socket_missing(environment):
    try:
        Path(_socket_path(environment)).lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False
