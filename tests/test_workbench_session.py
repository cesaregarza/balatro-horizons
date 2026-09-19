"""Connection lifecycle checks use simulated sockets and no Windows processes."""

import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.engine import native
from balatro_horizons.engine import windows_context as context
from balatro_horizons.review.service import ReviewService
from balatro_horizons.service import RunService


@pytest.fixture
def registration(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "ROOT", tmp_path)
    monkeypatch.setattr(context, "require_socket", Mock())
    return {"WSL_INTEROP": "/run/WSL/123_interop", "USERPROFILE": "/profile",
            "APPDATA": "/appdata", "LOCALAPPDATA": "/localappdata"}


def test_registration_filters_secrets_and_refreshes_without_process_mutation(registration, monkeypatch):
    spawn = Mock(side_effect=AssertionError("must not start or stop processes"))
    monkeypatch.setattr("subprocess.Popen", spawn)
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/999_interop")
    context.register_session({**registration, "OPENAI_API_KEY": "secret", "WSLENV": "OPENAI_API_KEY"})
    first = context.bridge_environment()
    assert first["WSL_INTEROP"] == registration["WSL_INTEROP"]
    assert "OPENAI_API_KEY" not in first and "secret" not in context.context_path().read_text()
    assert context.context_path().stat().st_mode & 0o777 == 0o600
    context.register_session({**registration, "WSL_INTEROP": "/run/WSL/456_interop"})
    assert context.bridge_environment()["WSL_INTEROP"] == "/run/WSL/456_interop"
    spawn.assert_not_called()


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


@pytest.mark.parametrize("socket", ["/tmp/123_interop", "/run/WSL/../123_interop", "/run/WSL/unexpected"])
def test_socket_registration_is_restricted(registration, socket):
    with pytest.raises(ValueError, match="INVALID_WINDOWS_INTEROP_SOCKET"):
        context.session_environment({**registration, "WSL_INTEROP": socket})


@pytest.mark.parametrize("mode,owner", [(stat.S_IFREG, 0), (stat.S_IFLNK, 0), (stat.S_IFSOCK, -1)])
def test_socket_must_be_owned_and_real(monkeypatch, mode, owner):
    monkeypatch.setattr(context.Path, "lstat", lambda self: SimpleNamespace(st_mode=mode, st_uid=owner))
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


def test_browser_preflight_is_operator_only_and_creates_no_episode(store, config, registration):
    app = create_app(store.root, config)
    with TestClient(app) as client:
        assert client.get("/api/operator/runtime").status_code == 403
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        assert not client.get("/api/operator/runtime", headers=headers).json()["ready"]
        response = client.post("/api/runs", headers=headers, json={"agent": "human", "offline": False})
        assert response.status_code == 400 and response.json()["error"] == "WINDOWS_SESSION_NOT_CONFIGURED"
        assert store.list_episodes() == [] and app.state.runs.thread is None


def test_bridge_subprocesses_receive_only_registered_environment(registration, monkeypatch):
    context.register_session({**registration, "OPENAI_API_KEY": "never-forward"})
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward")
    expected = context.bridge_environment()
    environment = SimpleNamespace(powershell="powershell.exe", port=17777, timeout_seconds=1, runtime="/none")
    bridge = native.WindowsBridge(environment)
    launch = Mock()
    command = Mock(return_value=SimpleNamespace(returncode=0, stdout="{}"))
    monkeypatch.setattr(native.subprocess, "Popen", launch)
    monkeypatch.setattr(native.subprocess, "run", command)

    bridge.command("launch")
    bridge.command("stop")
    assert launch.call_args.kwargs["env"] == expected
    assert command.call_args.kwargs["env"] == expected
    assert "OPENAI_API_KEY" not in expected

    launch.side_effect = OSError(2, "simulated process creation failure")
    with pytest.raises(native.NativeFailure, match="WINDOWS_BRIDGE_OS_ERROR_2"):
        bridge.command("rpc", "{}\n")
    assert launch.call_args.kwargs["env"] == expected
