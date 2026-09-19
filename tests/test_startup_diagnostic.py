import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from balatro_horizons.engine.native import NativeFailure

spec = importlib.util.spec_from_file_location(
    "startup_diagnostic", Path(__file__).resolve().parents[1] / "scripts/diagnose_native_startup.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_metadata_omits_private_contents_and_detects_stale_loader_log(tmp_path):
    assert module.launch_metadata(tmp_path) == {"process_record_present": False}
    (tmp_path / "process.json").write_text(
        json.dumps({"started": "2026-09-15T06:38:37.7545519Z", "instance_id": "secret-nonce"})
    )
    (tmp_path / "stderr.log").write_text("secret-log-content")
    log = tmp_path / "Mods/lovely/log/test.log"
    log.parent.mkdir(parents=True)
    log.write_text("secret-native-state")
    os.utime(log, (1, 1))
    result = module.launch_metadata(tmp_path)
    assert result["lovely_log_after_launch"] is False
    assert "secret" not in json.dumps(result)
    os.utime(log, (2_000_000_000, 2_000_000_000))
    assert module.launch_metadata(tmp_path)["lovely_log_after_launch"] is True


def test_restart_requires_certificate_and_cleans_failed_launch(tmp_path, monkeypatch):
    bridge = Mock(root=tmp_path)
    check = Mock(side_effect=NativeFailure("NATIVE_CAPABILITY_CERTIFICATE_MISMATCH"))
    monkeypatch.setattr(module, "require_environment_certificate", check)
    with pytest.raises(NativeFailure, match="CERTIFICATE_MISMATCH"):
        module.restart(bridge)
    bridge.stop.assert_not_called()
    bridge.launch.assert_not_called()
    check.side_effect = None
    (tmp_path / "process.json").write_text(json.dumps({"instance_id": "registered-instance"}))
    bridge.launch.side_effect = NativeFailure("NATIVE_STARTUP_HANDSHAKE_TIMEOUT")
    with pytest.raises(NativeFailure, match="HANDSHAKE_TIMEOUT"):
        module.restart(bridge)
    assert bridge.instance_id == "registered-instance"
    assert bridge.stop.call_count == 2


def test_probe_closes_transport_on_failed_identity(tmp_path):
    bridge = Mock(root=tmp_path)
    bridge.verify_identity.side_effect = NativeFailure("SAVE_ISOLATION_FAILED")
    result = module.probe(bridge)
    assert result["status"] == "failed"
    assert result["reason"] == "SAVE_ISOLATION_FAILED"
    bridge._close_rpc.assert_called_once()


@pytest.mark.parametrize("errors,calls,success", [(1, 2, True), (3, 3, False)])
def test_launch_retries_only_pre_submission_eio(monkeypatch, errors, calls, success):
    from balatro_horizons.config import Environment
    from balatro_horizons.engine import native

    spawn = Mock(side_effect=[OSError(5, "EIO")] * errors + [Mock()])
    monkeypatch.setattr(native.subprocess, "Popen", spawn)
    monkeypatch.setattr(native, "bridge_environment", lambda: {"PATH": "/test"})
    monkeypatch.setattr(native.time, "sleep", Mock())
    bridge = native.WindowsBridge(Environment())
    if success:
        assert bridge.command("launch") == {"launch_requested": True}
    else:
        with pytest.raises(NativeFailure, match="WINDOWS_BRIDGE_OS_ERROR_5"):
            bridge.command("launch")
    assert spawn.call_count == calls


def test_launch_does_not_retry_other_errors(monkeypatch):
    from balatro_horizons.config import Environment
    from balatro_horizons.engine import native

    spawn = Mock(side_effect=OSError(13, "denied"))
    monkeypatch.setattr(native.subprocess, "Popen", spawn)
    monkeypatch.setattr(native, "bridge_environment", lambda: {"PATH": "/test"})
    with pytest.raises(NativeFailure, match="WINDOWS_BRIDGE_OS_ERROR_13"):
        native.WindowsBridge(Environment()).command("launch")
    spawn.assert_called_once()


def test_workbench_probe_uses_observation_identity_and_stops_without_model(monkeypatch):
    from balatro_horizons import operator_client
    from balatro_horizons.storage import journal

    eid = "diagnostic-episode"
    terminal = {"provider_calls": 0, "cost_usd": 0, "outcome": "OPERATOR_ABORT"}
    store = Mock()
    store.summary.side_effect = [None, None, terminal, terminal]
    store.manifest.return_value = {"evaluation_eligible": False}
    monkeypatch.setattr(journal, "Store", Mock(return_value=store))
    persist = Mock()
    monkeypatch.setattr(journal, "atomic_json", persist)
    request = Mock(
        side_effect=[
            {"episode_id": eid},
            {
                "waiting": True,
                "context": {"observation": {"episode_id": eid, "phase": "BLIND_SELECT"}},
            },
            {"stop_requested": True},
        ]
    )
    monkeypatch.setattr(operator_client, "operator_request", request)
    assert module.workbench_startup("smoke") == 0
    assert request.call_args_list[0].kwargs["payload"]["agent"] == "human"
    assert request.call_args_list[-1].args == ("/stop",)
    assert persist.call_args.args[1]["native_ready"] is True
