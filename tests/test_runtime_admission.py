"""Offline regressions for session expiry and competing worker admission."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from balatro_horizons import service
from balatro_horizons.api import create_app
from balatro_horizons.cli.doctor import doctor
from balatro_horizons.evaluation.batches import plan_batch
from balatro_horizons.evidence import certification
from balatro_horizons.game import transport, windows_context
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.environment import Environment
from balatro_horizons.storage.journal import atomic_json


def test_batch_api_freezes_native_kind_before_session_preflight(store, config, monkeypatch):
    app = create_app(store.root, config)
    launch = Mock()
    monkeypatch.setattr(app.state.runs, "_launch", launch)
    monkeypatch.setattr(service, "load_session", Mock(side_effect=ValueError("WINDOWS_SESSION_EXPIRED")))
    plan = plan_batch(store, config, {"seeds": ["MOCK"]}, ["heuristic"])
    batch_id = plan["batch_id"]
    with TestClient(app) as client:
        headers = {"X-BH-Operator": app.state.operator_token}
        native = client.post(f"/api/batches/{batch_id}/run", headers=headers, json={"offline": False})
        assert native.status_code == 400
        assert native.json()["error"] == "WINDOWS_SESSION_EXPIRED"
        offline = client.post(f"/api/batches/{batch_id}/run", headers=headers, json={"offline": True})
        assert offline.status_code == 400
        assert offline.json()["error"] == "BATCH_EVIDENCE_KIND_CHANGED"
    assert json.loads((store.root / "batches" / batch_id / "execution.json").read_text()) == {"evidence_kind": "NATIVE"}
    assert store.list_episodes() == []
    launch.assert_not_called()


@pytest.mark.parametrize("endpoint,payload", [
    ("/api/runs", {"agent": "heuristic", "offline": True}),
    ("/api/batches/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/run", {"offline": True}),
    ("/api/verify", {"episode_id": "busy", "decision": 0}),
])
def test_worker_reservation_returns_busy_without_waiting(store, workbench_config, endpoint, payload):
    app = create_app(store.root, workbench_config)
    with TestClient(app) as client, app.state.runs._guard:
        response = client.post(endpoint, headers={"X-BH-Operator": app.state.operator_token}, json=payload)
        assert response.status_code == 400
        assert response.json()["error"] == "WORKER_BUSY"


@pytest.mark.parametrize("code", sorted(transport.SESSION_ERROR_CODES))
def test_handshake_does_not_hide_session_expiry_as_timeout(monkeypatch, code):
    monkeypatch.setattr(transport, "bridge_environment", lambda: {})
    sleep = Mock()
    monkeypatch.setattr(transport.time, "sleep", sleep)
    bridge = transport.WindowsBridge(Environment())
    bridge.verify_files = Mock(return_value={})
    bridge._spawn_with_retry = Mock()
    bridge.rpc = Mock(side_effect=NativeFailure(code))
    with pytest.raises(NativeFailure, match=code):
        bridge.launch()
    bridge.rpc.assert_called_once()
    sleep.assert_not_called()


def test_missing_session_precedes_native_files_and_process_launch(monkeypatch):
    monkeypatch.setattr(transport, "bridge_environment", Mock(side_effect=ValueError("WINDOWS_SESSION_NOT_CONFIGURED")))
    bridge = transport.WindowsBridge(Environment())
    bridge.verify_files = Mock()
    bridge._spawn_with_retry = Mock()
    with pytest.raises(NativeFailure, match="WINDOWS_SESSION_NOT_CONFIGURED"):
        bridge.launch()
    bridge.verify_files.assert_not_called()
    bridge._spawn_with_retry.assert_not_called()


def test_native_branch_expiry_creates_no_child(store, config, monkeypatch):
    parent = store.create({"evidence_kind": "NATIVE", "agent": "heuristic"}, {"seed": "MOCK"})
    runs = service.RunService(store, Mock())
    prepare = Mock(side_effect=AssertionError("child must not be prepared"))
    monkeypatch.setattr(service, "prepare_branch", prepare)
    monkeypatch.setattr(service, "load_session", Mock(side_effect=ValueError("WINDOWS_SESSION_EXPIRED")))
    with pytest.raises(ValueError, match="WINDOWS_SESSION_EXPIRED"):
        runs.branch(config, parent, 0, "human_takeover")
    prepare.assert_not_called()
    assert len(store.list_episodes()) == 1
    assert runs.thread is None


def test_live_doctor_reports_session_blocker_once_without_rpc(config, monkeypatch):
    monkeypatch.setattr(windows_context, "load_session", Mock(side_effect=ValueError("WINDOWS_SESSION_EXPIRED")))
    monkeypatch.setattr(transport.WindowsBridge, "verify_files", lambda self: {"game_version": "test"})
    monkeypatch.setattr(certification, "require_environment_certificate", lambda *args: {})
    rpc = Mock(side_effect=AssertionError("unavailable session must not open RPC"))
    monkeypatch.setattr(transport.WindowsBridge, "rpc", rpc)
    result = doctor(config, live=True)
    assert result["blockers"].count("WINDOWS_SESSION_EXPIRED") == 1
    assert result["offline_ready"]
    assert result["windows_connection"]["code"] == "WINDOWS_SESSION_EXPIRED"
    rpc.assert_not_called()


def test_later_repetition_expiry_preserves_selected_certificate(store, config, episode, monkeypatch, tmp_path):
    certificate = certification.verify_checkpoint(store, config, episode, 0)
    checkpoint = certification.read_checkpoint(store, episode, 0)
    checkpoint["game"] = {"kind": "native", "seed": "MOCK", "environment": {}}
    atomic_json(store.episode_path(episode, True) / "checkpoint-0.json", checkpoint)
    before = list(store.episode_path(episode, True).glob("certificate-record-*.json"))
    monkeypatch.setattr(certification, "ROOT", tmp_path)
    # Admission, first repetition, then expiry before the next fresh process.
    session = Mock(side_effect=[{}, {}, ValueError("WINDOWS_SESSION_EXPIRED")])
    replay = Mock(return_value=None)
    monkeypatch.setattr(certification, "load_session", session)
    monkeypatch.setattr(certification, "_replay_once", replay)
    with pytest.raises(ValueError, match="WINDOWS_SESSION_EXPIRED"):
        certification.verify_checkpoint(store, config, episode, 0)
    assert session.call_count == 3
    replay.assert_called_once()
    assert json.loads(certification.certificate_path(store, episode, 0).read_text()) == certificate
    assert list(store.episode_path(episode, True).glob("certificate-record-*.json")) == before


def test_real_transport_failure_still_invalidates_previous_certificate(store, config, episode, monkeypatch, tmp_path):
    certification.verify_checkpoint(store, config, episode, 0)
    checkpoint = certification.read_checkpoint(store, episode, 0)
    checkpoint["game"] = {"kind": "native", "seed": "MOCK", "environment": {}}
    atomic_json(store.episode_path(episode, True) / "checkpoint-0.json", checkpoint)
    monkeypatch.setattr(certification, "ROOT", tmp_path)
    monkeypatch.setattr(certification, "load_session", lambda: {})
    monkeypatch.setattr(certification, "NativeGame", Mock(side_effect=NativeFailure("WINDOWS_BRIDGE_FAILED")))
    result = certification.verify_checkpoint(store, config, episode, 0)
    assert result["status"] == "failed"
    assert result["failures"][0]["reason"] == "WINDOWS_BRIDGE_FAILED"
    assert json.loads(certification.certificate_path(store, episode, 0).read_text()) == result


def test_expiry_after_admission_is_terminal_infrastructure_failure(store, config, monkeypatch):
    runs = service.RunService(store, Mock())
    monkeypatch.setattr(service, "load_session", lambda: {})
    create_game = Mock(side_effect=NativeFailure("WINDOWS_SESSION_EXPIRED"))
    monkeypatch.setattr(runs, "create_game", create_game)
    monkeypatch.setattr(service, "ROOT", store.root.parent)
    # Prompt bytes are supplied locally; the game constructor is never native.
    monkeypatch.setattr("balatro_horizons.harness.instructions.load_prompt", lambda root: b"test")
    with pytest.raises(NativeFailure, match="WINDOWS_SESSION_EXPIRED"):
        runs.execute(config, "heuristic", "MOCK", offline=False)
    rows = store.list_episodes()
    assert len(rows) == 1
    summary = rows[0]["summary"]
    assert summary["evidence_kind"] == "NATIVE"
    assert summary["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert summary["provider_calls"] == summary["committed_actions"] == summary["cost_usd"] == 0
