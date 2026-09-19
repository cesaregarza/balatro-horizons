"""Startup fault injection; no Windows calls, game launches or providers."""

from unittest.mock import Mock

import pytest

from balatro_horizons.config import Environment
from balatro_horizons.engine import native


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "ROOT", tmp_path)
    result = native.WindowsBridge(Environment())
    result.verify_files = Mock()
    result.command = Mock()
    result.rpc = Mock(return_value={})
    result.verify_identity = Mock()
    return result


def test_launcher_nonzero_exit_is_immediate_and_recorded(bridge):
    bridge._launch_process = Mock()
    bridge._launch_process.poll.return_value = 1
    with pytest.raises(native.NativeFailure, match="NATIVE_LAUNCH_PROCESS_FAILED"):
        bridge.launch()
    bridge.rpc.assert_not_called()
    assert bridge._startup_deadline is None
    import json

    report = json.loads(next((native.ROOT / "private/startup-failures").glob("*.json")).read_text())
    assert report["launcher_exit_code"] == 1
    assert report["last_handshake_error"] is None


def test_identity_failure_is_not_retried_or_hidden_by_timeout(bridge):
    bridge.verify_identity.side_effect = native.NativeFailure("NATIVE_PROCESS_IDENTITY_MISMATCH")
    with pytest.raises(native.NativeFailure, match="NATIVE_PROCESS_IDENTITY_MISMATCH"):
        bridge.launch()
    bridge.rpc.assert_called_once()
    bridge.verify_identity.assert_called_once()


def test_rpc_readiness_retry_does_not_repeat_launch(bridge, monkeypatch):
    monkeypatch.setattr(native.time, "sleep", Mock())
    bridge.rpc.side_effect = [native.NativeFailure("RPC_TRANSPORT_UNKNOWN"), {"ready": True}]
    assert bridge.launch() == {"ready": True}
    bridge.command.assert_called_once_with("launch")
    assert bridge.rpc.call_count == 2


def test_startup_reports_expired_session_without_retry(bridge):
    bridge.rpc.side_effect = native.NativeFailure("WINDOWS_SESSION_EXPIRED")
    with pytest.raises(native.NativeFailure, match="WINDOWS_SESSION_EXPIRED"):
        bridge.launch()
    bridge.rpc.assert_called_once()


def test_cleanup_failure_preserves_original_startup_error(monkeypatch):
    bridge = Mock()
    bridge.launch.side_effect = native.NativeFailure("NATIVE_LAUNCH_PROCESS_FAILED")
    bridge.stop.side_effect = native.NativeFailure("WINDOWS_SESSION_EXPIRED")
    monkeypatch.setattr(native, "WindowsBridge", Mock(return_value=bridge))
    with pytest.raises(native.NativeFailure, match="NATIVE_LAUNCH_PROCESS_FAILED"):
        native.NativeGame(Environment(), "SYNTHETIC_TEST", calibration=True)
    bridge.stop.assert_called_once()
