"""Private Windows bridge transport for the native game session."""

from __future__ import annotations

import errno
import json
import os
import re
import selectors
import subprocess
import time
import uuid
from pathlib import Path

from balatro_horizons.game.contract import ERROR_NAMES, RPC_METHODS, NativeFailure, NativeRejected
from balatro_horizons.game.environment import (
    Environment,
    verify_files,
    verify_identity,
    windows_runtime_path,
)
from balatro_horizons.game.windows_context import SESSION_ERROR_CODES, bridge_environment

_LUA_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,98}$")
_UPSTREAM_REJECTION_NAMES = frozenset({"BAD_REQUEST", "INVALID_STATE", "NOT_ALLOWED"})


def _safe_lua_code(value: object) -> str | None:
    return value if isinstance(value, str) and _LUA_CODE.fullmatch(value) else None


def _bridge_path(runtime: str) -> str:
    return windows_runtime_path(runtime).rstrip("\\/") + r"\bridge.ps1"


def _registered_environment() -> dict[str, str]:
    try:
        return bridge_environment()
    except ValueError as error:
        raise NativeFailure(str(error)) from None


def raise_rpc_error(error: object) -> None:
    """Classify endpoint errors; retain untrusted text only for private evidence."""
    payload = error if isinstance(error, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_message = data.get("message") if isinstance(data.get("message"), str) else None
    if raw_message is None and isinstance(payload.get("message"), str):
        raw_message = payload["message"]
    code = _safe_lua_code(raw_message)
    code = code or _safe_lua_code(data.get("code"))
    name = data.get("name") if isinstance(data.get("name"), str) else payload.get("name")
    name = name if isinstance(name, str) else None
    upstream_envelope = isinstance(data.get("message"), str) and isinstance(data.get("name"), str)
    expected = ERROR_NAMES.get(code)
    if expected is not None:
        if name == expected and name == "NOT_ALLOWED":
            raise NativeRejected(code, name=name, raw_message=raw_message)
        raise NativeFailure(code, name=name, raw_message=raw_message)
    # BalatroBot puts its own message and name together under data. Project
    # errors use the top-level message; an unknown project token claiming
    # NOT_ALLOWED is not trusted as a legality code.
    if name in _UPSTREAM_REJECTION_NAMES and (
        name != "NOT_ALLOWED" or upstream_envelope or code is None
    ):
        raise NativeRejected("NATIVE_ACTION_REJECTED", name=name, raw_message=raw_message)
    raise NativeFailure(code or "RPC_ENDPOINT_FAILURE", name=name, raw_message=raw_message)


class WindowsBridge:
    """Line-delimited JSON-RPC bridge with isolated launch identity."""

    def __init__(self, environment: Environment):
        self.env = environment
        self.root = Path(environment.runtime)
        self._rpc_process = None
        self.calibration = False
        self.lock: dict | None = None
        self.instance_id: str | None = None

    def _command(self, mode: str) -> list[str]:
        command = [
            self.env.powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            _bridge_path(self.env.runtime),
            "-Mode",
            mode,
            "-Runtime",
            self.env.windows_runtime,
            "-Port",
            str(self.env.port),
            "-TimeoutSec",
            str(self.env.http_timeout_seconds),
        ]
        if mode in ("launch", "stop") and self.instance_id:
            command.extend(["-InstanceId", self.instance_id])
        if mode == "launch" and self.calibration:
            command.append("-Calibration")
        return command

    @staticmethod
    def _spawn_with_retry(command: list[str], **kwargs):
        # WSL may return EIO opening PowerShell. No request was submitted yet,
        # so retrying process creation cannot duplicate a native action.
        for attempt in range(3):
            try:
                return subprocess.Popen(command, **kwargs)
            except OSError as error:
                if error.errno != errno.EIO or attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))
        raise AssertionError("unreachable")

    def _control(self, mode: str) -> dict:
        environment = _registered_environment()
        try:
            result = subprocess.run(
                self._command(mode),
                text=True,
                capture_output=True,
                timeout=self.env.launch_timeout_seconds,
                check=False,
                env=environment,
            )
        except OSError as error:
            raise NativeFailure(f"WINDOWS_BRIDGE_OS_ERROR_{error.errno}") from None
        except subprocess.TimeoutExpired:
            raise NativeFailure("WINDOWS_BRIDGE_TIMEOUT") from None
        if result.returncode:
            raise NativeFailure("WINDOWS_BRIDGE_FAILED")
        try:
            return json.loads(result.stdout.lstrip("\ufeff"))
        except (TypeError, ValueError):
            raise NativeFailure("WINDOWS_BRIDGE_RESPONSE_INVALID") from None

    def launch(self) -> dict:
        environment = _registered_environment()
        self.lock = self.verify_files()
        self.instance_id = uuid.uuid4().hex
        try:
            # A Windows GUI descendant can keep WSL's captured pipes open;
            # launch detached and verify readiness over the separate RPC pipe.
            self._spawn_with_retry(
                self._command("launch"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=environment,
            )
        except OSError as error:
            raise NativeFailure(f"WINDOWS_BRIDGE_OS_ERROR_{error.errno}") from None
        deadline = time.monotonic() + self.env.launch_timeout_seconds
        while time.monotonic() < deadline:
            try:
                state = self.rpc("bh_inspect")
                self.verify_identity(state)
                return state
            except (NativeFailure, NativeRejected) as error:
                if str(error) in SESSION_ERROR_CODES:
                    raise
                time.sleep(0.5)
        raise NativeFailure("NATIVE_STARTUP_HANDSHAKE_TIMEOUT")

    def _exchange(self, line: str):
        if self._rpc_process is None or self._rpc_process.poll() is not None:
            environment = _registered_environment()
            try:
                self._rpc_process = self._spawn_with_retry(
                    self._command("rpc"),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    env=environment,
                )
            except OSError as error:
                raise NativeFailure(f"WINDOWS_BRIDGE_OS_ERROR_{error.errno}") from None
        process = self._rpc_process
        try:
            process.stdin.write(line.encode("utf8"))
            process.stdin.flush()
            result = bytearray()
            # PowerShell must get its full HTTP timeout before Python closes
            # the pipe; preserve the margin for its bridge_error response.
            deadline = time.monotonic() + self.env.http_timeout_seconds + self.env.rpc_response_margin_seconds
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while b"\n" not in result:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise NativeFailure("WINDOWS_BRIDGE_TIMEOUT")
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        raise NativeFailure("WINDOWS_BRIDGE_CLOSED")
                    result.extend(chunk)
                    if len(result) > 16_000_000:
                        raise NativeFailure("NATIVE_RESPONSE_TOO_LARGE")
            return json.loads(result.decode("utf8").lstrip("\ufeff"))
        except NativeFailure:
            self._close_rpc()
            raise
        except (OSError, ValueError, TypeError):
            self._close_rpc()
            raise NativeFailure("RPC_TRANSPORT_UNKNOWN") from None

    def _close_rpc(self):
        if self._rpc_process is None:
            return
        process = self._rpc_process
        self._rpc_process = None
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        if process.stdout:
            process.stdout.close()

    def rpc(self, method: str, params: dict | None = None, request_id: str | None = None):
        if method not in RPC_METHODS:
            raise NativeFailure("METHOD_FORBIDDEN", name="HARNESS_FAULT")
        request = {
            "jsonrpc": "2.0",
            "id": request_id or uuid.uuid4().hex,
            "method": method,
            "params": params or {},
        }
        response = self._exchange(json.dumps(request) + "\n")
        if "bridge_error" in response:
            raise NativeFailure("RPC_TRANSPORT_UNKNOWN")
        if response.get("id") != request["id"]:
            raise NativeFailure("RPC_RESPONSE_ID_MISMATCH")
        if "error" in response:
            raise_rpc_error(response["error"])
        if "result" not in response:
            raise NativeFailure("RPC_RESPONSE_INVALID")
        return response["result"]

    def verify_files(self) -> dict:
        self.lock = verify_files(self.env)
        return self.lock

    def verify_identity(self, state: dict) -> None:
        verify_identity(
            state,
            instance_id=self.instance_id,
            lock=self.lock,
            calibration=self.calibration,
        )

    def stop(self) -> dict:
        self._close_rpc()
        try:
            return self._control("stop")
        finally:
            self.instance_id = None
