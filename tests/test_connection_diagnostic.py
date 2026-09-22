"""Connection evidence uses transport doubles, never Windows or providers."""

import json
from unittest.mock import Mock

import pytest

from balatro_horizons.cli import main
from balatro_horizons.evidence.collect import connection
from balatro_horizons.evidence.stages import plan
from balatro_horizons.game import windows_context as context
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.environment import Environment


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "ROOT", tmp_path)
    monkeypatch.setattr(context, "ROOT", tmp_path)
    monkeypatch.setattr(context, "require_socket", Mock())
    context.register_session({"WSL_INTEROP": "/run/WSL/123_interop", "USERPROFILE": "p",
                              "APPDATA": "a", "LOCALAPPDATA": "l"})
    (tmp_path / "native").mkdir()
    (tmp_path / "native/bridge.ps1").write_text("mock")
    identity = Mock(return_value={"commit": "test", "implementation_hash": "test"})
    monkeypatch.setattr(connection, "source_identity", identity)
    monkeypatch.setattr(connection, "require_source_instrumentation", Mock())
    state = {"bh": {"ready": True, "busy": False}}
    bridge = Mock(env=Environment(), instance_id="owned-nonce")
    bridge.launch.return_value = state
    bridge.verify_files.return_value = {"manifest": "pinned"}
    def rpc(method):
        assert method == "bh_inspect"
        try:
            context.load_session()
        except ValueError as error:
            raise NativeFailure(str(error)) from None
        return state
    bridge.rpc.side_effect = rpc
    bridge.stop.side_effect = lambda: setattr(bridge, "instance_id", None)
    monkeypatch.setattr(connection, "WindowsBridge", Mock(return_value=bridge))
    monkeypatch.setattr(connection, "runtime_state", Mock(return_value={"owned_processes": 0, "port_listening": False}))
    return bridge, identity, tmp_path / "receipt.json"


def test_connection_plan_is_one_launch_no_resets_or_certification(capsys):
    assert main(["evidence", "plan", "--connection-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["expected_physical_launches"] == 1
    assert result["expected_game_resets"] == 0
    assert not result["release_certification_requested"]
    assert not result["capability_activation_requested"]
    with pytest.raises(ValueError, match="MUTUALLY_EXCLUSIVE"):
        plan(connection_only=True, gameplay_only=True)


def test_receipt_binds_source_environment_and_same_process_rpc_recovery(harness):
    bridge, _, report = harness
    before = context.context_path().read_bytes()
    result = connection.collect(bridge.env, report)
    assert result["status"] == "passed"
    assert result["native_launches"] == result["launch_attempts"] == 1
    assert result["game_resets"] == result["provider_calls"] == result["cost_usd"] == 0
    assert result["unregistered_rpc_refused"] and result["rpc_reopened_same_game"]
    assert result["owned_process_stopped"] and not result["actual_socket_expiry_tested"]
    assert not result["capability_certificates_created"]
    assert result["source"]["commit"] == "test" and result["environment_hash"]
    assert json.loads(report.read_text()) == result and report.stat().st_mode & 0o777 == 0o600
    assert context.context_path().read_bytes() == before
    assert "owned-nonce" not in report.read_text() and "interop" not in report.read_text()
    bridge.launch.assert_called_once()
    bridge.stop.assert_called_once()
    assert bridge.rpc.call_count == 2


@pytest.mark.parametrize("state", [{"owned_processes": 1, "port_listening": False},
                                   {"owned_processes": 0, "port_listening": True}])
def test_busy_runtime_is_never_launched_or_stopped(harness, monkeypatch, state):
    bridge, _, report = harness
    monkeypatch.setattr(connection, "runtime_state", lambda env: state)
    result = connection.collect(bridge.env, report)
    assert result["status"] == "failed" and result["reason"] == "NATIVE_RUNTIME_BUSY"
    bridge.launch.assert_not_called()
    bridge.stop.assert_not_called()


def test_failed_recovery_restores_registration_stops_own_process_and_keeps_failure(harness):
    bridge, _, report = harness
    before = context.context_path().read_bytes()
    bridge.rpc.side_effect = NativeFailure("UNEXPECTED_TRANSPORT_FAILURE")
    result = connection.collect(bridge.env, report)
    assert result["status"] == "failed" and result["reason"] == "UNEXPECTED_TRANSPORT_FAILURE"
    assert context.context_path().read_bytes() == before
    bridge.stop.assert_called_once()
    assert json.loads(report.read_text())["status"] == "failed"


def test_failed_rpc_cleanup_still_stops_owned_game(harness):
    bridge, _, report = harness
    bridge._close_rpc.side_effect = OSError("private-path")
    result = connection.collect(bridge.env, report)
    bridge.stop.assert_called_once()
    assert result["status"] == "failed" and result["reason"] == "OSError"
    assert "private-path" not in report.read_text()


def test_source_drift_is_not_a_pass(harness):
    bridge, identity, report = harness
    identity.side_effect = [{"commit": "before"}, {"commit": "after"}]
    result = connection.collect(bridge.env, report)
    assert result["status"] == "failed"
    assert result["reason"] == "CONNECTION_SOURCE_OR_ENVIRONMENT_CHANGED"


def test_existing_report_is_not_overwritten_and_does_not_launch(harness):
    bridge, _, report = harness
    report.write_text("preserved")
    with pytest.raises(ValueError, match="NEW_LINUX_PATH"):
        connection.collect(bridge.env, report)
    assert report.read_text() == "preserved"
    bridge.launch.assert_not_called()


def test_connection_cli_requires_report_before_any_native_action(monkeypatch):
    collect = Mock()
    monkeypatch.setattr(connection, "collect", collect)
    with pytest.raises(SystemExit) as error:
        main(["native", "diagnose", "--connection"])
    assert error.value.code == 2
    collect.assert_not_called()


def test_process_status_uses_registered_environment_and_only_safe_fields(monkeypatch):
    monkeypatch.setattr(connection, "bridge_environment", lambda: {"PATH": "safe"})
    run = Mock(return_value=Mock(returncode=0, stdout='{"owned_processes":0,"port_listening":false,"private":"omit"}'))
    monkeypatch.setattr(connection.subprocess, "run", run)
    assert connection.runtime_state(Environment()) == {"owned_processes": 0, "port_listening": False}
    assert run.call_args.kwargs["env"] == {"PATH": "safe"}
    assert run.call_args.kwargs["timeout"] == 30


def test_readiness_wait_is_read_only_and_bounded(monkeypatch):
    bridge = Mock(env=Environment(launch_timeout_seconds=1))
    bridge.rpc.return_value = {"bh": {"ready": True, "busy": False}}
    monkeypatch.setattr(connection.time, "sleep", Mock())
    monkeypatch.setattr(connection.time, "monotonic", Mock(side_effect=[0, 0.5]))
    connection.wait_ready(bridge, {"bh": {"ready": False, "busy": False}})
    bridge.rpc.assert_called_once_with("bh_inspect")
    assert bridge.verify_identity.call_count == 2
    bridge.rpc.reset_mock()
    monkeypatch.setattr(connection.time, "monotonic", Mock(side_effect=[0, 1]))
    with pytest.raises(ValueError, match="CONNECTION_READINESS_TIMEOUT"):
        connection.wait_ready(bridge, {"bh": {"ready": False, "busy": False}})
    bridge.rpc.assert_not_called()


def test_busy_native_lock_prevents_process_creation(harness, monkeypatch):
    bridge, _, report = harness
    monkeypatch.setattr(connection.fcntl, "flock", Mock(side_effect=BlockingIOError))
    result = connection.collect(bridge.env, report)
    assert result["status"] == "failed" and result["reason"] == "NATIVE_WORKER_BUSY"
    bridge.launch.assert_not_called()
    bridge.stop.assert_not_called()


def test_main_menu_connection_does_not_claim_gameplay_readiness():
    bridge = Mock(env=Environment())
    bridge.rpc = Mock(side_effect=AssertionError("MENU connection readiness must not poll for gameplay readiness"))
    state = {"state": "MENU", "bh": {"ready": False, "busy": False}}
    result = connection.wait_ready(bridge, state)
    assert result == {"phase": "MENU", "gameplay_ready": False}
    bridge.verify_identity.assert_called_once_with(state)
    bridge.rpc.assert_not_called()


def test_busy_menu_is_not_a_passing_connection(monkeypatch):
    bridge = Mock(env=Environment(launch_timeout_seconds=1))
    monkeypatch.setattr(connection.time, "monotonic", Mock(side_effect=[0, 1]))
    with pytest.raises(ValueError, match="CONNECTION_READINESS_TIMEOUT"):
        connection.wait_ready(bridge, {"state": "MENU", "bh": {"ready": False, "busy": True}})
