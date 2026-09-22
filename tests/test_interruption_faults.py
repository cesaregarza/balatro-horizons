"""Offline contracts for calibration-only interruption helpers."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from balatro_horizons.evidence.collect import interruption_faults as faults
from balatro_horizons.game.contract import NativeFailure


def test_debugger_exit_policy_immediately_follows_successful_attach():
    script = faults._SUSPEND_TEMPLATE
    attach = 'if (!DebugActiveProcess(process.Id)) throw new InvalidOperationException("DEBUG_ATTACH_FAILED");'
    policy = 'if (!DebugSetProcessKillOnExit(false)) throw new InvalidOperationException("DEBUG_KILL_POLICY_FAILED");'
    before, after = script.split(attach, 1)
    between, after_policy = after.split(policy, 1)
    # Set the local flag first so a failed policy call still detaches in finally;
    # no process access, output, or other operation belongs in this window.
    assert between.strip() == "attached = true;"
    assert "process.Refresh();" in before
    assert "process.MainModule.FileName" in before
    assert 'process.StartTime.ToUniversalTime().ToString("o")' in before
    assert "Console.WriteLine" not in before
    assert "Console.WriteLine" in after_policy
    assert "bool detached = !attached || DebugActiveProcessStop(process.Id);" in after_policy


class FakeStream:
    def __init__(self, calls):
        self.calls = calls

    def write(self, value):
        self.calls.append("write")
        return len(value)

    def flush(self):
        self.calls.append("flush")

    def close(self):
        self.calls.append("close")


class FakeProcess:
    def __init__(self, calls):
        self.calls = calls
        self.stdin = FakeStream(calls)
        self.stdout = object()
        self.returncode = 0

    def poll(self):
        return None

    def kill(self):
        self.calls.append("kill")

    def wait(self, timeout=None):
        self.calls.append("wait")


class FakeBridge:
    calibration = True
    instance_id = "a" * 32

    def __init__(self, calls, status="unknown", ack=False, status_error=False):
        self.calls = calls
        self.status = status
        self.ack = ack
        self.status_error = status_error
        self._rpc_process = FakeProcess(calls)

    def verify_identity(self, state):
        self.calls.append("verify")

    def rpc(self, method, params=None, request_id=None):
        if method == "bh_inspect":
            self.calls.append("inspect")
            return {"instance_id": self.instance_id, "bh": {"ready": True, "busy": False}}
        if method == "select":
            self._rpc_process.stdin.write(b"request")
            self._rpc_process.stdin.flush()
            if self.ack:
                return {"accepted": True}
            raise NativeFailure("WINDOWS_BRIDGE_CLOSED")
        if method == "bh_request_status":
            self.calls.append("status")
            if self.status_error:
                raise NativeFailure("STATUS_QUERY_FAILED")
            return {"status": self.status}
        self.calls.append(method)
        return {}


class FakeGame:
    _closed = False

    def __init__(self, bridge):
        self.bridge = bridge
        self.environment = SimpleNamespace(
            powershell="powershell", windows_runtime="C:\\OwnedRuntime"
        )

    @contextmanager
    def intercept_rpc_for_calibration(self, wrapper):
        original = self.bridge.rpc
        self.bridge.rpc = wrapper(original)
        try:
            yield
        finally:
            self.bridge.rpc = original


def test_cut_action_ack_writes_once_and_records_status():
    calls = []
    game = FakeGame(FakeBridge(calls, status="unknown"))
    evidence = {}

    with faults.cut_action_ack(game, evidence):
        with pytest.raises(NativeFailure, match="WINDOWS_BRIDGE_CLOSED"):
            game.bridge.rpc("select", {}, "request-1")
        assert game.bridge.rpc("bh_request_status", {"request_id": "request-1"}) == {
            "status": "unknown"
        }

    assert evidence == {
        "action_rpc_sends": 1,
        "cut_after_flush": True,
        "status_queries": 1,
        "request_id": "request-1",
        "request_status": "unknown",
        "transport_error": "WINDOWS_BRIDGE_CLOSED",
    }
    assert calls.count("write") == 1
    assert calls.index("flush") < calls.index("kill")


def test_cut_action_ack_refuses_non_calibration_game():
    bridge = FakeBridge([])
    bridge.calibration = False
    with pytest.raises(NativeFailure, match="EVALUATOR_ONLY"):
        with faults.cut_action_ack(FakeGame(bridge), {}):
            pass


def test_cut_action_ack_rejects_acknowledgement_as_injection_failure():
    bridge = FakeBridge([], ack=True)
    evidence = {}
    with pytest.raises(ValueError, match="ACK_CUT_NOT_OBSERVED"):
        with faults.cut_action_ack(FakeGame(bridge), evidence):
            bridge.rpc("select", {}, "request-1")
    assert evidence["transport_error"] == "ACK_CUT_NOT_OBSERVED"


def test_cut_is_not_claimed_when_owned_rpc_termination_fails(monkeypatch):
    bridge = FakeBridge([])
    def denied():
        raise PermissionError("private path")
    monkeypatch.setattr(bridge._rpc_process, "kill", denied)
    original = bridge._rpc_process.stdin
    evidence = {}
    with pytest.raises(PermissionError):
        with faults.cut_action_ack(FakeGame(bridge), evidence):
            bridge.rpc("select", {}, "request-1")
    assert evidence["cut_after_flush"] is False
    assert evidence["transport_error"] == "PermissionError"
    assert bridge._rpc_process.stdin is original


def test_cut_action_ack_counts_failed_status_query_attempt():
    bridge = FakeBridge([], status_error=True)
    evidence = {}
    with faults.cut_action_ack(FakeGame(bridge), evidence):
        with pytest.raises(NativeFailure, match="WINDOWS_BRIDGE_CLOSED"):
            bridge.rpc("select", {}, "request-1")
        with pytest.raises(NativeFailure, match="STATUS_QUERY_FAILED"):
            bridge.rpc("bh_request_status", {"request_id": "request-1"})
    assert evidence["status_queries"] == 1
    assert evidence["request_status"] == "invalid"


def test_cut_action_ack_rejects_closed_game_before_identity_rpc():
    game = FakeGame(FakeBridge([]))
    game._closed = True
    with pytest.raises(NativeFailure, match="NATIVE_GAME_CLOSED"):
        with faults.cut_action_ack(game, {}):
            pass


def test_read_line_rejects_partial_receipt_at_eof(monkeypatch):
    class Selector:
        def register(self, stream, event):
            pass

        def select(self, timeout):
            return [True]

        def close(self):
            pass

    chunks = iter((b"{", b'"held":true', b""))
    stream = SimpleNamespace(fileno=lambda: 1)
    monkeypatch.setattr(faults.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(faults.os, "read", lambda fd, size: next(chunks))
    with pytest.raises(NativeFailure, match="INTERRUPTION_HELPER_CLOSED"):
        faults._read_line(SimpleNamespace(stdout=stream), faults.time.monotonic() + 1)


def test_suspend_owned_game_restores_helper_and_preserves_primary(monkeypatch):
    calls = []
    game = FakeGame(FakeBridge(calls))
    process = FakeProcess(calls)
    receipts = iter(({"held": True, "pid": 41}, {"detached": True, "pid": 41}))
    monkeypatch.setattr(faults.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(faults, "bridge_environment", lambda: {"PATH": "safe"})
    monkeypatch.setattr(faults, "_read_line", lambda proc, deadline: next(receipts))
    evidence = {}

    with pytest.raises(ValueError, match="body-failure") as raised:
        with faults.suspend_owned_game(game, evidence):
            assert evidence["held"] and evidence["owned_pid"] == 41
            raise ValueError("body-failure")

    assert str(raised.value) == "body-failure"
    assert evidence["detached"] is True


def test_suspend_cleanup_note_does_not_replace_primary(monkeypatch):
    game = FakeGame(FakeBridge([]))
    process = FakeProcess([])
    receipts = iter(({"held": True, "pid": 41},))
    monkeypatch.setattr(faults.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(faults, "bridge_environment", lambda: {})
    monkeypatch.setattr(faults, "_read_line", lambda proc, deadline: next(receipts))
    monkeypatch.setattr(
        faults,
        "_finish_helper",
        lambda proc, owned_pid: (_ for _ in ()).throw(NativeFailure("DEBUG_DETACH_FAILED")),
    )

    with pytest.raises(ValueError, match="body-failure") as raised:
        with faults.suspend_owned_game(game, {}):
            raise ValueError("body-failure")
    assert any("INTERRUPTION_HELPER_CLEANUP_FAILED" in note for note in raised.value.__notes__)


def test_suspend_rejects_wrong_detached_pid(monkeypatch):
    game = FakeGame(FakeBridge([]))
    process = FakeProcess([])
    receipts = iter(({"held": True, "pid": 41}, {"detached": True, "pid": 42}))
    monkeypatch.setattr(faults.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(faults, "bridge_environment", lambda: {})
    monkeypatch.setattr(faults, "_read_line", lambda proc, deadline: next(receipts))
    with pytest.raises(NativeFailure, match="DEBUG_DETACH_FAILED"):
        with faults.suspend_owned_game(game, {}):
            pass


def test_suspend_early_helper_failure_stays_primary(monkeypatch):
    game = FakeGame(FakeBridge([]))
    process = FakeProcess([])
    errors = iter((NativeFailure("HELPER_EARLY_FAILURE"), NativeFailure("HELPER_CLEANUP_FAILURE")))
    monkeypatch.setattr(faults.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(faults, "bridge_environment", lambda: {})
    def fail_read(proc, deadline):
        raise next(errors)

    monkeypatch.setattr(faults, "_read_line", fail_read)
    with pytest.raises(NativeFailure, match="HELPER_EARLY_FAILURE") as raised:
        with faults.suspend_owned_game(game, {}):
            pass
    assert any("INTERRUPTION_HELPER_CLEANUP_FAILED" in note for note in raised.value.__notes__)


@pytest.mark.parametrize("status", ["committed", "unknown", "pending"])
def test_real_native_adapter_reconciles_once_without_resend(monkeypatch, status):
    from balatro_horizons.game import session

    game = session.NativeGame.__new__(session.NativeGame)
    game._closed = False
    game.environment = SimpleNamespace(timeout_seconds=2)
    calls = []
    game.bridge = FakeBridge(calls, status=status)
    game.raw = {}
    monkeypatch.setattr(session, "native_request", lambda *args: ("select", {}))
    evidence = {}
    with faults.cut_action_ack(game, evidence):
        if status == "committed":
            game.apply_public_action(None, None, "request-1")
        else:
            with pytest.raises(NativeFailure, match="ACTION_STATUS_UNKNOWN"):
                game.apply_public_action(None, None, "request-1")
    assert evidence["action_rpc_sends"] == evidence["status_queries"] == 1
    assert evidence["request_id"] == "request-1"
    assert evidence["request_status"] == status
    assert calls.count("write") == 1
    assert calls.count("inspect") == (2 if status == "committed" else 1)


def test_receipt_reader_rejects_nonobject_and_times_out_partial_line(monkeypatch):
    class Selector:
        def register(self, stream, event):
            pass

        def select(self, timeout):
            return [True]

        def close(self):
            pass

    monkeypatch.setattr(faults.selectors, "DefaultSelector", Selector)
    stream = SimpleNamespace(fileno=lambda: 1)
    monkeypatch.setattr(faults.os, "read", lambda *args: b"[]\n")
    with pytest.raises(NativeFailure, match="RECEIPT_INVALID"):
        faults._read_line(SimpleNamespace(stdout=stream), faults.time.monotonic() + 1)
    ticks = iter((0, 2))
    monkeypatch.setattr(faults.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(faults.os, "read", lambda *args: b"{")
    with pytest.raises(NativeFailure, match="HELPER_TIMEOUT"):
        faults._read_line(SimpleNamespace(stdout=stream), 1)
