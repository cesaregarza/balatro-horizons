"""Calibration-only interruption helpers for an already owned native game."""

import json
import os
import re
import selectors
import subprocess
import time
from contextlib import contextmanager

from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.windows_context import bridge_environment

_INSTANCE_ID = re.compile(r"^[a-f0-9]{32}$")
_STATUS = frozenset({"committed", "rejected", "unknown", "pending"})
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,98}$")


def _error_code(error):
    value = str(error)
    return value if _CODE.fullmatch(value) else type(error).__name__


def _require_game(game):
    if getattr(game, "_closed", False):
        raise NativeFailure("NATIVE_GAME_CLOSED")
    bridge = getattr(game, "bridge", None)
    if bridge is None or not getattr(bridge, "calibration", False):
        raise NativeFailure("EVALUATOR_ONLY")
    instance_id = getattr(bridge, "instance_id", None)
    if not isinstance(instance_id, str) or not _INSTANCE_ID.fullmatch(instance_id):
        raise NativeFailure("NATIVE_SESSION_REQUIRED")
    process = getattr(bridge, "_rpc_process", None)
    if process is None or process.poll() is not None or process.stdin is None:
        raise NativeFailure("NATIVE_SESSION_UNAVAILABLE")
    bridge.verify_identity(bridge.rpc("bh_inspect"))
    return bridge


class _CutInput:
    def __init__(self, stream, process, evidence):
        self._stream = stream
        self._process = process
        self._evidence = evidence

    def write(self, value):
        result = self._stream.write(value)
        self._evidence["action_rpc_sends"] += 1
        return result

    def flush(self):
        self._stream.flush()
        self._process.kill()
        self._evidence["cut_after_flush"] = True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _record_status(evidence, result):
    status = result.get("status") if isinstance(result, dict) else None
    evidence["request_status"] = status if status in _STATUS else "invalid"


@contextmanager
def cut_action_ack(game, evidence):
    """Cut one owned RPC stdin after an action write, without resending it."""
    bridge = _require_game(game)
    evidence.update({
        "action_rpc_sends": 0,
        "cut_after_flush": False,
        "status_queries": 0,
    })
    targeted = False

    def wrapper(original):
        def request(method, params=None, request_id=None):
            nonlocal targeted
            if method == "bh_request_status":
                evidence["status_queries"] += 1
                try:
                    result = original(method, params, request_id)
                except BaseException:
                    evidence["request_status"] = "invalid"
                    raise
                _record_status(evidence, result)
                return result
            if method != "select" or targeted:
                return original(method, params, request_id)
            targeted = True
            if isinstance(request_id, str) and request_id:
                evidence["request_id"] = request_id
            process = bridge._rpc_process
            if process is None or process.poll() is not None or process.stdin is None:
                raise NativeFailure("NATIVE_SESSION_UNAVAILABLE")
            stream = process.stdin
            process.stdin = _CutInput(stream, process, evidence)
            try:
                result = original(method, params, request_id)
            except BaseException as error:
                evidence["transport_error"] = _error_code(error)
                raise
            else:
                evidence["transport_error"] = "ACK_CUT_NOT_OBSERVED"
                raise ValueError("ACK_CUT_NOT_OBSERVED")
            finally:
                process.stdin = stream

        return request

    try:
        with game.intercept_rpc_for_calibration(wrapper):
            yield
    except BaseException as error:
        evidence.setdefault("transport_error", _error_code(error))
        raise
    finally:
        if not targeted:
            evidence.setdefault("transport_error", "TARGET_ACTION_NOT_SENT")


_SUSPEND_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
$runtime = '__RUNTIME__'
$expected = Join-Path $runtime 'Balatro.exe'
$record = Get-Content (Join-Path $runtime 'process.json') -Raw | ConvertFrom-Json
$process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
if (-not $process -or $record.instance_id -ne '__INSTANCE__' -or $process.Path -ne $expected -or
    $process.StartTime.ToUniversalTime().ToString('o') -ne $record.started) {
  throw 'OWNED_GAME_IDENTITY_MISMATCH'
}
$temp = Join-Path $runtime ('bh-interruption-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
$env:TEMP = $temp
$env:TMP = $temp
try {
Add-Type @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
public static class BhDebug {
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool DebugActiveProcess(int id);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool DebugActiveProcessStop(int id);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool DebugSetProcessKillOnExit(bool kill);
  public static void Hold(Process process, string expectedPath, string expectedStart) {
    IntPtr ownedHandle = process.Handle;
    process.Refresh();
    if (process.HasExited || process.MainModule.FileName != expectedPath ||
        process.StartTime.ToUniversalTime().ToString("o") != expectedStart) {
      throw new InvalidOperationException("OWNED_GAME_IDENTITY_MISMATCH");
    }
    bool attached = false;
    try {
      if (!DebugActiveProcess(process.Id)) throw new InvalidOperationException("DEBUG_ATTACH_FAILED");
      attached = true;
      if (!DebugSetProcessKillOnExit(false)) throw new InvalidOperationException("DEBUG_KILL_POLICY_FAILED");
      Console.WriteLine("{\"held\":true,\"pid\":" + process.Id + ",\"hold_seconds\":8}");
      Console.Out.Flush();
      Thread.Sleep(8000);
    } finally {
      bool detached = !attached || DebugActiveProcessStop(process.Id);
      if (!detached) {
        Console.WriteLine("{\"detached\":false,\"pid\":" + process.Id + "}");
        Console.Out.Flush();
        throw new InvalidOperationException("DEBUG_DETACH_FAILED");
      }
      GC.KeepAlive(ownedHandle);
      GC.KeepAlive(process);
    }
    Console.WriteLine("{\"detached\":true,\"pid\":" + process.Id + "}");
    Console.Out.Flush();
  }
}
'@
  [BhDebug]::Hold($process, $expected, $record.started)
} finally {
  if ($null -eq $oldTemp) { Remove-Item Env:TEMP -ErrorAction SilentlyContinue }
  else { $env:TEMP = $oldTemp }
  if ($null -eq $oldTmp) { Remove-Item Env:TMP -ErrorAction SilentlyContinue }
  else { $env:TMP = $oldTmp }
  Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
  $process.Dispose()
}
"""


def _suspend_script(runtime, instance_id):
    return _SUSPEND_TEMPLATE.replace("__RUNTIME__", runtime.replace("'", "''")).replace(
        "__INSTANCE__", instance_id
    )


def _read_line(process, deadline):
    selector = selectors.DefaultSelector()
    data = bytearray()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while b"\n" not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise NativeFailure("INTERRUPTION_HELPER_TIMEOUT")
            chunk = os.read(process.stdout.fileno(), 1)
            if not chunk:
                raise NativeFailure("INTERRUPTION_HELPER_CLOSED")
            data.extend(chunk)
            if len(data) > 4096:
                raise NativeFailure("INTERRUPTION_HELPER_RECEIPT_TOO_LARGE")
    finally:
        selector.close()
    try:
        receipt = json.loads(bytes(data))
        if not isinstance(receipt, dict):
            raise ValueError
        return receipt
    except (TypeError, ValueError):
        raise NativeFailure("INTERRUPTION_HELPER_RECEIPT_INVALID") from None


def _close_output(process):
    stream = getattr(process, "stdout", None)
    if stream is not None:
        try:
            stream.close()
        except (AttributeError, OSError, ValueError):
            pass


def _finish_helper(process, owned_pid):
    try:
        receipt = _read_line(process, time.monotonic() + 15)
    finally:
        _close_output(process)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
        raise NativeFailure("INTERRUPTION_HELPER_EXIT_TIMEOUT") from None
    if (
        not isinstance(receipt, dict)
        or receipt.get("detached") is not True
        or receipt.get("pid") != owned_pid
        or process.returncode != 0
    ):
        raise NativeFailure("DEBUG_DETACH_FAILED")


def _kill_helper(process):
    try:
        process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        _close_output(process)


@contextmanager
def suspend_owned_game(game, evidence):
    """Hold one identity-verified owned game for a bounded native hang probe."""
    bridge = _require_game(game)
    runtime = str(game.environment.windows_runtime)
    command = _suspend_script(runtime, bridge.instance_id)
    process = None
    primary = None
    owned_pid = None
    evidence.update({"held": False, "detached": False, "hold_seconds": 8})
    try:
        process = subprocess.Popen(
            [game.environment.powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            env=bridge_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        held = _read_line(process, time.monotonic() + 30)
        if not isinstance(held, dict) or held.get("held") is not True or type(held.get("pid")) is not int:
            raise NativeFailure("INTERRUPTION_HELPER_RECEIPT_INVALID")
        owned_pid = held["pid"]
        evidence.update({"held": True, "owned_pid": owned_pid})
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        if process is not None:
            try:
                _finish_helper(process, owned_pid)
                evidence["detached"] = True
            except BaseException as cleanup:
                _kill_helper(process)
                if primary is not None:
                    primary.add_note("INTERRUPTION_HELPER_CLEANUP_FAILED: " + _error_code(cleanup))
                else:
                    raise
