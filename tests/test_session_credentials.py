import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.cli import credentials
from balatro_horizons.evidence.certification import (
    certificate_path,
    read_checkpoint,
    verify_checkpoint,
)
from balatro_horizons.game import transport
from balatro_horizons.game import windows_context as context
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.environment import Environment
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService
from balatro_horizons.storage.journal import atomic_json


@pytest.fixture
def registration(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "ROOT", tmp_path)
    monkeypatch.setattr(context, "require_socket", Mock())
    return {
        "WSL_INTEROP": "/run/WSL/123_interop",
        "USERPROFILE": "/profile",
        "APPDATA": "/appdata",
        "LOCALAPPDATA": "/localappdata",
    }


def test_session_environment_filters_secrets_proxies_and_unsafe_wslenv(registration):
    result = context.session_environment(
        {**registration, "OPENAI_API_KEY": "secret", "HTTP_PROXY": "proxy", "WSLENV": "secret"}
    )
    assert "OPENAI_API_KEY" not in result and "HTTP_PROXY" not in result
    assert "OPENAI_API_KEY" not in result["WSLENV"]
    assert "APPDATA/p" in result["WSLENV"]


def test_wslenv_preserves_exact_windows_path_flags(registration):
    result = context.session_environment(
        {
            **registration,
            "SYSTEMDRIVE": "C:",
            "USERPROFILE": "C:/Users/test",
            "APPDATA": "C:/Users/test/AppData",
            "LOCALAPPDATA": "C:/Users/test/AppData/Local",
        }
    )
    assert result["WSLENV"] == (
        "SYSTEMDRIVE:USERPROFILE/p:APPDATA/p:LOCALAPPDATA/p"
    )


def test_session_environment_rejects_missing_and_injected_values(registration):
    with pytest.raises(ValueError, match="WINDOWS_SESSION_NOT_CONFIGURED"):
        context.session_environment({})
    with pytest.raises(ValueError, match="INVALID_SESSION_ENVIRONMENT"):
        context.session_environment({**registration, "APPDATA": "/bad\nExecStart=x"})


def test_registration_is_private_and_refreshes_without_process_mutation(registration, monkeypatch):
    spawn = Mock(side_effect=AssertionError("must not start or stop processes"))
    monkeypatch.setattr("subprocess.Popen", spawn)
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/999_interop")
    context.register_session({**registration, "OPENAI_API_KEY": "secret"})
    assert context.bridge_environment()["WSL_INTEROP"] == registration["WSL_INTEROP"]
    assert "secret" not in context.context_path().read_text()
    assert stat.S_IMODE(context.context_path().stat().st_mode) == 0o600
    assert context.connection_status()["ready"]
    spawn.assert_not_called()


def test_invalid_registration_and_invalid_utf8_fail_closed(registration):
    context.register_session(registration)
    path = context.context_path()
    path.write_bytes(b"\xff")
    assert context.connection_status()["code"] == "WINDOWS_SESSION_REGISTRATION_INVALID"


def test_expiry_and_reconnection_report_health_without_leaking_paths(registration):
    assert context.connection_status()["code"] == "WINDOWS_SESSION_NOT_CONFIGURED"
    context.register_session(registration)
    context.require_socket.side_effect = ValueError("WINDOWS_SESSION_EXPIRED")
    result = context.connection_status()
    assert not result["ready"] and result["code"] == "WINDOWS_SESSION_EXPIRED"
    assert "/profile" not in json.dumps(result)
    context.require_socket.side_effect = None
    context.register_session({**registration, "WSL_INTEROP": "/run/WSL/456_interop"})
    assert context.connection_status()["ready"]
    assert context.bridge_environment()["WSL_INTEROP"] == "/run/WSL/456_interop"


@pytest.mark.parametrize("change", ["public", "symlink", "corrupt"])
def test_invalid_registration_fails_closed(registration, change):
    context.register_session(registration)
    path = context.context_path()
    if change == "public":
        path.chmod(0o644)
    elif change == "symlink":
        target = path.with_suffix(".saved")
        path.rename(target)
        path.symlink_to(target)
    else:
        path.write_text("[]")
    assert context.connection_status()["code"] == "WINDOWS_SESSION_REGISTRATION_INVALID"


@pytest.mark.parametrize(
    "socket", ["/tmp/123_interop", "/run/WSL/../123_interop", "/run/WSL/unexpected"]
)
def test_socket_registration_is_restricted(registration, socket):
    with pytest.raises(ValueError, match="INVALID_WINDOWS_INTEROP_SOCKET"):
        context.session_environment({**registration, "WSL_INTEROP": socket})


@pytest.mark.parametrize("mode,owner", [(stat.S_IFREG, 0), (stat.S_IFLNK, 0), (stat.S_IFSOCK, -1)])
def test_socket_must_be_owned_and_real(monkeypatch, mode, owner):
    monkeypatch.setattr(
        context.Path, "lstat", lambda self: SimpleNamespace(st_mode=mode, st_uid=owner)
    )
    monkeypatch.setattr(os, "getuid", lambda: 0)
    with pytest.raises(ValueError, match="WINDOWS_SESSION_EXPIRED"):
        context.require_socket({"WSL_INTEROP": "/run/WSL/123_interop"})


def test_expired_connection_rejects_before_episode_or_thread(store, config, registration):
    service = RunService(store, ReviewService(store))
    service._launch = Mock(side_effect=AssertionError("must not start worker"))
    with pytest.raises(ValueError, match="WINDOWS_SESSION_NOT_CONFIGURED"):
        service.start(config, "human", "TEST", offline=False)
    with pytest.raises(ValueError, match="WINDOWS_SESSION_NOT_CONFIGURED"):
        service.execute(config, "human", "TEST", offline=False)
    assert store.list_episodes() == []
    service._launch.assert_not_called()


def test_browser_preflight_is_operator_only_and_creates_no_episode(
    store, config, registration
):
    app = create_app(store.root, config)
    with TestClient(app) as client:
        assert client.get("/api/operator/runtime").status_code == 403
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        assert not client.get("/api/operator/runtime", headers=headers).json()["ready"]
        response = client.post(
            "/api/runs", headers=headers, json={"agent": "human", "offline": False}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "WINDOWS_SESSION_NOT_CONFIGURED"
        assert store.list_episodes() == [] and app.state.runs.thread is None


def test_verify_unavailable_session_preserves_selected_certificate(
    store, workbench_config, episode, monkeypatch
):
    checkpoint = read_checkpoint(store, episode, 0)
    checkpoint["game"]["kind"] = "native"
    atomic_json(store.episode_path(episode, True) / "checkpoint-0.json", checkpoint)
    certificate = certificate_path(store, episode, 0)
    atomic_json(certificate, {"status": "passed", "marker": "retain"})
    selected = certificate.read_bytes()
    monkeypatch.setattr(
        "balatro_horizons.workbench.routes.load_session",
        Mock(side_effect=ValueError("WINDOWS_SESSION_NOT_CONFIGURED")),
    )
    verify = Mock(side_effect=AssertionError("verification must not start"))
    monkeypatch.setattr("balatro_horizons.workbench.routes.verify_checkpoint", verify)
    app = create_app(store.root, workbench_config)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        response = client.post(
            "/api/verify",
            headers=headers,
            json={"episode_id": episode, "decision": 0, "mode": "checkpoint"},
        )
    assert response.status_code == 400
    assert response.json()["error"] == "WINDOWS_SESSION_NOT_CONFIGURED"
    assert certificate.read_bytes() == selected
    verify.assert_not_called()


def test_direct_native_verify_requires_session_before_replay(store, config, episode, monkeypatch):
    checkpoint = read_checkpoint(store, episode, 0)
    checkpoint["game"]["kind"] = "native"
    atomic_json(store.episode_path(episode, True) / "checkpoint-0.json", checkpoint)
    certificate = certificate_path(store, episode, 0)
    atomic_json(certificate, {"status": "passed", "marker": "retain"})
    selected = certificate.read_bytes()
    monkeypatch.setattr(
        "balatro_horizons.evidence.certification.load_session",
        Mock(side_effect=ValueError("WINDOWS_SESSION_EXPIRED")),
    )
    with pytest.raises(ValueError, match="WINDOWS_SESSION_EXPIRED"):
        verify_checkpoint(store, config, episode, 0)
    assert certificate.read_bytes() == selected


def test_native_verify_expiry_after_preflight_keeps_certificate(
    store, config, episode, monkeypatch
):
    checkpoint = read_checkpoint(store, episode, 0)
    checkpoint["game"]["kind"] = "native"
    checkpoint["game"]["seed"] = "SIMULATED"
    atomic_json(store.episode_path(episode, True) / "checkpoint-0.json", checkpoint)
    certificate = certificate_path(store, episode, 0)
    atomic_json(certificate, {"status": "passed", "marker": "retain"})
    selected = certificate.read_bytes()
    session = Mock(return_value={})
    monkeypatch.setattr("balatro_horizons.evidence.certification.load_session", session)
    game = Mock(side_effect=NativeFailure("WINDOWS_SESSION_EXPIRED"))
    monkeypatch.setattr("balatro_horizons.evidence.certification.NativeGame", game)
    with pytest.raises(ValueError, match="WINDOWS_SESSION_EXPIRED"):
        verify_checkpoint(store, config, episode, 0)
    assert session.call_count == 2
    game.assert_called_once()
    assert certificate.read_bytes() == selected


def test_bridge_processes_receive_only_registered_environment(registration, monkeypatch):
    context.register_session({**registration, "OPENAI_API_KEY": "never-forward"})
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "never-forward")
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, "never-forward")
    expected = context.bridge_environment()
    environment = Environment(
        powershell="powershell.exe",
        runtime="/none",
        port=17777,
        timeout_seconds=1,
        launch_timeout_seconds=1,
        http_timeout_seconds=1,
    )
    bridge = transport.WindowsBridge(environment)
    bridge.verify_files = Mock(return_value={})
    bridge.rpc = Mock(return_value={"state": "MENU", "bh": {}})
    bridge.verify_identity = Mock()
    launch = Mock()
    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    bridge.launch()
    assert launch.call_args.kwargs["env"] == expected
    assert "OPENAI_API_KEY" not in launch.call_args.kwargs["env"]
    assert "ANTHROPIC_API_KEY" not in launch.call_args.kwargs["env"]
    assert not any(name in launch.call_args.kwargs["env"] for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"
    ))

    command = Mock(return_value=SimpleNamespace(returncode=0, stdout="{}"))
    monkeypatch.setattr(transport.subprocess, "run", command)
    bridge.stop()
    assert command.call_args.kwargs["env"] == expected

    launch.side_effect = OSError(2, "simulated process creation failure")
    with pytest.raises(NativeFailure, match="WINDOWS_BRIDGE_OS_ERROR_2"):
        bridge._exchange("{}\n")
    assert launch.call_args.kwargs["env"] == expected


def test_credentials_preview_and_apply_never_echo_values(tmp_path):
    source = tmp_path / "source.env"
    source.write_text("OPENAI_API_KEY=mock-secret-not-a-key\n")
    root, units = tmp_path / "repo", tmp_path / "units"
    preview = credentials.configure(root, units, source)
    assert not root.exists() and not units.exists()
    assert preview == {
        "applied": False,
        "credential_names": ["OPENAI_API_KEY"],
        "credential_count": 1,
        "drop_in_written": False,
        "service_restart_required": False,
        "provider_calls": 0,
    }
    result = credentials.configure(root, units, source, apply=True)
    target = root / "private/providers.env"
    dropin = units / "balatro-horizons.service.d/20-provider-environment.conf"
    assert target.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(dropin.stat().st_mode) == 0o600
    assert dropin.read_text() == f"[Service]\nEnvironmentFile=\nEnvironmentFile={target}\n"
    assert "mock-secret" not in json.dumps(result)
    assert str(root) not in json.dumps(result) and str(units) not in json.dumps(result)
    source.unlink()
    credentials.configure(root, units, apply=True)
    assert target.read_text() == "OPENAI_API_KEY=mock-secret-not-a-key\n"


def test_empty_preparation_allows_unpaid_backend_without_changing_existing_keys(tmp_path):
    root, units = tmp_path / "repo", tmp_path / "units"
    result = credentials.configure(root, units, apply=True)
    assert result["credential_names"] == [] and result["provider_calls"] == 0
    assert (root / "private/providers.env").exists()
    assert (units / "balatro-horizons.service.d/20-provider-environment.conf").exists()


@pytest.mark.parametrize(
    "data",
    [
        b"PATH=/evil",
        b"OPENAI_API_KEY=",
        b"OPENAI_API_KEY='broken",
        b"OPENAI_API_KEY=one\nOPENAI_API_KEY=two",
        b"OPENAI_API_KEY=a b",
        b"\xff",
    ],
)
def test_credentials_reject_invalid_input_without_echoing(data):
    with pytest.raises(ValueError) as error:
        credentials.credential_names(data)
    assert data.decode("utf-8", errors="replace") not in str(error.value)


def test_credentials_refuse_symlink_destination(tmp_path):
    target = tmp_path / "providers.env"
    other = tmp_path / "untouched"
    other.write_text("unchanged")
    target.symlink_to(other)
    with pytest.raises(ValueError, match="SYMLINK"):
        credentials.atomic_private(target, b"new")
    assert other.read_text() == "unchanged"
