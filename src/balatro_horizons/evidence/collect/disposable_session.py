"""Bounded disposable WSL bridge used by the session-expiry evidence collector."""

from __future__ import annotations

import json
import os
import pwd
import re
import secrets
import select
import selectors
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.collect.disposable_identity import (
    _child_ancestors,
    _error_code,
    _socket_missing,
    _socket_stat,
    _validate_receipt,
)
from balatro_horizons.game import windows_context

WSL_EXE = "/mnt/c/Windows/System32/wsl.exe"
CHILD_MODULE = "balatro_horizons.evidence.collect.disposable_session"
MAX_RECEIPT_BYTES = 4096
MAX_CHILD_INPUT_BYTES = 128
CHILD_INPUT_TIMEOUT_SECONDS = 180
CHILD_EXIT_TIMEOUT_SECONDS = 20
SOCKET_EXPIRY_TIMEOUT_SECONDS = 10
_NONCE = re.compile(r"^[0-9a-f]{32}$")


def _native_path(value):
    try:
        resolved = Path(value).resolve()
    except (OSError, RuntimeError, TypeError):
        raise ValueError("DISPOSABLE_NATIVE_ROOT_REQUIRED") from None
    if resolved == Path("/mnt") or resolved.is_relative_to("/mnt"):
        raise ValueError("DISPOSABLE_NATIVE_ROOT_REQUIRED")


def _safe_child_environment(environment):
    try:
        registration = windows_context.session_environment(environment)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(_error_code(error, "DISPOSABLE_SESSION_ENVIRONMENT_INVALID")) from None
    allowed = (
        set(getattr(windows_context, "WINDOWS_VARIABLES", {}))
        | set(getattr(windows_context, "SESSION_VARIABLES", ()))
        | {"WSLENV"}
    )
    result = {key: value for key, value in registration.items() if key in allowed}
    result.update({key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ})
    if any(
        not isinstance(value, str) or any(c in value for c in "\x00\r\n")
        for value in result.values()
    ):
        raise ValueError("DISPOSABLE_SESSION_ENVIRONMENT_INVALID")
    return result


def _launch_values(environment):
    distro = environment.get("WSL_DISTRO_NAME") if isinstance(environment, dict) else None
    if (
        not isinstance(distro, str)
        or not distro
        or len(distro) > 255
        or any(c in distro for c in "\x00\r\n")
    ):
        raise ValueError("DISPOSABLE_DISTRO_INVALID")
    try:
        username = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        raise ValueError("DISPOSABLE_USER_INVALID") from None
    if not isinstance(username, str) or not username or any(c in username for c in "\x00\r\n"):
        raise ValueError("DISPOSABLE_USER_INVALID")
    for path in (ROOT, sys.executable):
        _native_path(path)
    return distro, username


def _child_command(distro, username, nonce):
    return [
        WSL_EXE,
        "--distribution",
        distro,
        "--user",
        username,
        "--cd",
        str(ROOT),
        "--exec",
        sys.executable,
        "-m",
        CHILD_MODULE,
        "--child",
        nonce,
    ]


def _read_line(process):
    stream = getattr(process, "stdout", None)
    if stream is None:
        raise ValueError("DISPOSABLE_HELPER_RESPONSE_INVALID")
    selector = selectors.DefaultSelector()
    data = bytearray()
    try:
        try:
            selector.register(stream, selectors.EVENT_READ)
        except (OSError, TypeError, ValueError):
            raise ValueError("DISPOSABLE_HELPER_RESPONSE_INVALID") from None
        deadline = time.monotonic() + 30
        while b"\n" not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ValueError("DISPOSABLE_HELPER_RESPONSE_TIMEOUT")
            try:
                chunk = os.read(stream.fileno(), min(1024, MAX_RECEIPT_BYTES + 1 - len(data)))
            except (OSError, ValueError):
                raise ValueError("DISPOSABLE_HELPER_RESPONSE_INVALID") from None
            if not chunk:
                raise ValueError("DISPOSABLE_HELPER_RESPONSE_CLOSED")
            data.extend(chunk)
            if len(data) > MAX_RECEIPT_BYTES:
                raise ValueError("DISPOSABLE_HELPER_RESPONSE_TOO_LARGE")
    finally:
        selector.close()
    try:
        receipt = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ValueError("DISPOSABLE_HELPER_RESPONSE_INVALID") from None
    if not isinstance(receipt, dict):
        raise ValueError("DISPOSABLE_HELPER_RESPONSE_INVALID")
    return receipt


class _Disposable:
    def __init__(self, environment, row):
        self._original = dict(environment)
        self._row = row
        self._process = None
        self._nonce = None
        self._operator = None
        self._socket = None
        self._closed = False
        self.environment = dict(environment)

    def _operator_unchanged(self):
        current = _socket_stat(self._original)
        if any(current[key] != self._operator[key] for key in ("path", "inode", "uid", "_dev")):
            raise ValueError("DISPOSABLE_OPERATOR_SOCKET_CHANGED")

    def _send_exit(self, nonce):
        if self._process is None or self._process.stdin is None:
            return
        stream = self._process.stdin
        try:
            if nonce:
                stream.write((self._nonce + "\n").encode("ascii"))
                stream.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def _close_stdout(self):
        stream = getattr(self._process, "stdout", None)
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def _wait_exit(self):
        try:
            code = self._process.wait(timeout=CHILD_EXIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ValueError("DISPOSABLE_CHILD_EXIT_TIMEOUT") from None
        except (OSError, ValueError):
            raise ValueError("DISPOSABLE_CHILD_EXIT_FAILED") from None
        self._close_stdout()
        if code != 0:
            raise ValueError("DISPOSABLE_CHILD_EXIT_FAILED")
        self._row["normal_exit_completed"] = True

    def _wait_socket_expiry(self):
        deadline = time.monotonic() + SOCKET_EXPIRY_TIMEOUT_SECONDS
        while True:
            self._operator_unchanged()
            if _socket_missing(self.environment):
                self._row["actual_socket_expired"] = True
                return
            if time.monotonic() >= deadline:
                raise ValueError("DISPOSABLE_SOCKET_NOT_EXPIRED")
            time.sleep(0.05)

    def start(self):
        self._operator = _socket_stat(self._original)
        self._row["operator_socket_identity"] = {
            key: self._operator[key] for key in ("path", "inode", "uid")
        }
        distro, username = _launch_values(self._original)
        self._nonce = secrets.token_hex(16)
        try:
            self._process = subprocess.Popen(
                _child_command(distro, username, self._nonce),
                env=_safe_child_environment(self._original),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ValueError("DISPOSABLE_LAUNCH_FAILED") from None
        receipt = _read_line(self._process)
        if self._process.poll() is not None:
            raise ValueError("DISPOSABLE_HELPER_EXITED_EARLY")
        admission = _validate_receipt(receipt, self._nonce, os.getuid(), distro)
        child_socket = _socket_stat({"WSL_INTEROP": admission["socket"]["path"]})
        if (child_socket["inode"], child_socket["uid"]) != (
            admission["socket"]["inode"],
            admission["socket"]["uid"],
        ) or (admission["dev"] is not None and child_socket["_dev"] != admission["dev"]):
            raise ValueError("DISPOSABLE_SOCKET_IDENTITY_CHANGED")
        if child_socket["path"] == self._operator["path"] or (
            child_socket["_dev"],
            child_socket["inode"],
        ) == (self._operator["_dev"], self._operator["inode"]):
            raise ValueError("DISPOSABLE_SHARED_SOCKET")
        self._operator_unchanged()
        self._socket = child_socket
        self.environment = dict(self._original)
        self.environment["WSL_INTEROP"] = child_socket["path"]
        self._row.update(
            socket_identity={key: child_socket[key] for key in ("path", "inode", "uid")},
            separate_operator_socket=True,
            child_pid=admission["child_pid"],
            relay_pid=admission["relay_pid"],
            child_start_ticks=admission["child_start_ticks"],
            relay_start_ticks=admission["relay_start_ticks"],
        )

    def expire(self):
        if self._closed:
            if self._row.get("actual_socket_expired"):
                return
            raise ValueError("DISPOSABLE_SESSION_CLOSED")
        if self._process is None:
            raise ValueError("DISPOSABLE_SESSION_UNAVAILABLE")
        if self._row.get("actual_socket_expired"):
            return
        self._operator_unchanged()
        self._send_exit(True)
        self._wait_exit()
        self._wait_socket_expiry()
        self._closed = True

    def cleanup(self):
        if self._process is None:
            return
        self._send_exit(False)
        if self._process.poll() is None:
            self._wait_exit()
        elif self._process.returncode != 0:
            self._close_stdout()
            raise ValueError("DISPOSABLE_CHILD_EXIT_FAILED")
        else:
            self._close_stdout()
            self._row["normal_exit_completed"] = True
        if self._socket is None:
            self._operator_unchanged()
            self._closed = True
            return
        self._wait_socket_expiry()
        self._closed = True


@contextmanager
def disposable_session(environment: dict, row: dict):
    if not isinstance(environment, dict) or not isinstance(row, dict):
        raise ValueError("DISPOSABLE_SESSION_ARGUMENT_INVALID")
    row.update(
        separate_operator_socket=False, normal_exit_completed=False, actual_socket_expired=False
    )
    session = _Disposable(environment, row)
    primary = None
    try:
        session.start()
        yield session
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            session.cleanup()
        except BaseException as cleanup:
            row["cleanup_reason"] = _error_code(cleanup)
            if primary is None:
                raise
            primary.add_note("DISPOSABLE_CLEANUP_FAILED: " + _error_code(cleanup))


def _child_main(arguments):
    if len(arguments) != 2 or arguments[0] != "--child" or not _NONCE.fullmatch(arguments[1]):
        return 2
    nonce = arguments[1]
    try:
        child_environment = {
            key: os.environ[key] for key in ("WSL_INTEROP", "WSL_DISTRO_NAME") if key in os.environ
        }
        identity = _socket_stat(child_environment)
        ancestors = _child_ancestors()
        if not ancestors or ancestors[0]["pid"] != os.getpid():
            return 3
        receipt = {
            "nonce": nonce,
            "distro": os.environ.get("WSL_DISTRO_NAME"),
            "socket": {
                **{key: identity[key] for key in ("path", "inode", "uid")},
                "dev": identity["_dev"],
            },
            "uid": os.getuid(),
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "ancestors": ancestors,
        }
        encoded = (json.dumps(receipt, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_RECEIPT_BYTES:
            return 4
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        ready, _, _ = select.select([sys.stdin.fileno()], [], [], CHILD_INPUT_TIMEOUT_SECONDS)
        if not ready:
            return 5
        data = os.read(sys.stdin.fileno(), MAX_CHILD_INPUT_BYTES + 1)
        if len(data) > MAX_CHILD_INPUT_BYTES:
            return 6
        if not data:
            return 0
        try:
            return 0 if data.decode("ascii").rstrip("\r\n") == nonce else 8
        except UnicodeDecodeError:
            return 7
    except (OSError, ValueError, TypeError, KeyError):
        return 9


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
