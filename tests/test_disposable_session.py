"""Offline admission and cleanup tests for the disposable socket helper."""

import io
import json
import os
from types import SimpleNamespace

import pytest

from balatro_horizons.evidence.collect import disposable_identity as identity
from balatro_horizons.evidence.collect import disposable_session as helper


class _Input:
    def __init__(self, on_close):
        self.values = []
        self.closed = False
        self._on_close = on_close

    def write(self, value):
        self.values.append(value)
        return len(value)

    def flush(self):
        return None

    def close(self):
        if not self.closed:
            self.closed = True
            self._on_close()


class _Process:
    def __init__(self, receipt, on_close, wait_code=0, wait_error=None):
        reader, writer = os.pipe()
        os.write(writer, receipt)
        os.close(writer)
        self.stdout = os.fdopen(reader, "rb")
        self.stdin = _Input(on_close)
        self.returncode = None
        self._wait_code = wait_code
        self._wait_error = wait_error
        self.wait_calls = []
        self.kill_calls = 0
        self.terminate_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self._wait_error is not None:
            raise self._wait_error
        self.returncode = self._wait_code
        return self.returncode


@pytest.fixture
def setup_helper(tmp_path, monkeypatch):
    child_pid = os.getpid()
    child_stat = identity._proc_stat(child_pid)
    relay_pid = child_stat["ppid"]
    operator_path = f"/run/WSL/{relay_pid + 1000000}_interop"
    child_path = f"/run/WSL/{relay_pid}_interop"
    monkeypatch.setattr(helper.windows_context, "require_socket", lambda environment: None)
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    infos = {
        operator_path: SimpleNamespace(st_mode=0o140000, st_uid=os.getuid(), st_ino=41, st_dev=1),
        child_path: SimpleNamespace(st_mode=0o140000, st_uid=os.getuid(), st_ino=42, st_dev=1),
    }
    monkeypatch.setattr(
        helper.Path,
        "lstat",
        lambda path: infos[str(path)] if str(path) in infos else (_ for _ in ()).throw(FileNotFoundError),
    )

    def receipt_for(nonce, path=child_path):
        info = infos[str(path)]
        current = identity._proc_stat(child_pid)
        relay = identity._proc_stat(relay_pid)
        return (
            json.dumps(
                {
                    "nonce": nonce,
                    "distro": "Ubuntu",
                    "socket": {
                        "path": str(path),
                        "inode": info.st_ino,
                        "uid": os.getuid(),
                        "dev": info.st_dev,
                    },
                    "uid": os.getuid(),
                    "pid": child_pid,
                    "ppid": current["ppid"],
                    "ancestors": [current, relay],
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )

    original = {
        "WSL_INTEROP": operator_path,
        "WSL_DISTRO_NAME": "Ubuntu",
        "USERPROFILE": "private",
        "APPDATA": "appdata",
        "LOCALAPPDATA": "localappdata",
        "WSLENV": "OPENAI_API_KEY:BAD/p",
        "OPENAI_API_KEY": "must-not-cross",
    }
    yield original, infos, child_path, receipt_for, child_pid, relay_pid


def test_socket_identity_calls_registration_check_and_returns_safe_fields(setup_helper):
    environment, _, _, _, _, _ = setup_helper
    result = identity.socket_identity(environment)
    assert set(result) == {"path", "inode", "uid"}
    assert result["path"] == environment["WSL_INTEROP"]
    assert result["uid"] == os.getuid()


def test_exact_wsl_command_safe_environment_and_normal_expiry(setup_helper, monkeypatch):
    environment, infos, child_path, receipt_for, _, relay_pid = setup_helper
    process_box = {}

    def popen(command, **kwargs):
        process_box["command"] = command
        process_box["kwargs"] = kwargs
        process = _Process(receipt_for(command[-1]), lambda: infos.pop(str(child_path), None))
        process_box["process"] = process
        return process

    monkeypatch.setattr(helper.subprocess, "Popen", popen)
    row = {}
    with helper.disposable_session(environment, row) as session:
        assert session.environment["WSL_INTEROP"] == str(child_path)
        assert environment["WSL_INTEROP"] != session.environment["WSL_INTEROP"]
        session.expire()
    command = process_box["command"]
    assert command == [
        helper.WSL_EXE,
        "--distribution",
        "Ubuntu",
        "--user",
        helper.pwd.getpwuid(os.getuid()).pw_name,
        "--cd",
        str(helper.ROOT),
        "--exec",
        helper.sys.executable,
        "-m",
        helper.CHILD_MODULE,
        "--child",
        command[-1],
    ]
    child_env = process_box["kwargs"]["env"]
    assert "OPENAI_API_KEY" not in child_env
    assert child_env["WSL_INTEROP"] == environment["WSL_INTEROP"]
    assert "OPENAI_API_KEY" not in child_env["WSLENV"]
    assert "APPDATA/p" in child_env["WSLENV"]
    assert row["separate_operator_socket"]
    assert row["relay_pid"] == relay_pid
    assert row["normal_exit_completed"] and row["actual_socket_expired"]
    assert process_box["process"].stdout.closed
    assert process_box["process"].kill_calls == process_box["process"].terminate_calls == 0


def test_invalid_bounded_response_still_closes_child(setup_helper, monkeypatch):
    environment, infos, child_path, _, _, _ = setup_helper
    process_box = {}

    def popen(command, **kwargs):
        process = _Process(
            b"x" * (helper.MAX_RECEIPT_BYTES + 1) + b"\n",
            lambda: infos.pop(str(child_path), None),
        )
        process_box["process"] = process
        return process

    monkeypatch.setattr(helper.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="DISPOSABLE_HELPER_RESPONSE_TOO_LARGE"):
        with helper.disposable_session(environment, {}):
            pass
    assert process_box["process"].stdin.closed
    assert process_box["process"].wait_calls
    assert process_box["process"].kill_calls == process_box["process"].terminate_calls == 0


def test_shared_socket_is_refused_and_child_is_closed(setup_helper, monkeypatch):
    environment, infos, child_path, receipt_for, _, _ = setup_helper
    environment["WSL_INTEROP"] = child_path

    def popen(command, **kwargs):
        receipt = receipt_for(command[-1], path=child_path)
        return _Process(receipt, lambda: infos.pop(str(child_path), None))

    monkeypatch.setattr(helper.subprocess, "Popen", popen)
    row = {}
    with pytest.raises(ValueError, match="DISPOSABLE_SHARED_SOCKET"):
        with helper.disposable_session(environment, row):
            pass
    assert not row["separate_operator_socket"]


def test_ancestry_and_nonce_uid_validation(monkeypatch):
    current = {"pid": 20, "ppid": 1, "start_ticks": 30}
    monkeypatch.setattr(identity, "_proc_stat", lambda pid: current)
    good = {
        "nonce": "a" * 32,
        "uid": 0,
        "pid": 20,
        "ppid": 1,
        "socket": {"path": "/run/WSL/1_interop", "inode": 40, "uid": 0},
        "ancestors": [current, {"pid": 1, "ppid": 0, "start_ticks": 31}],
    }
    monkeypatch.setattr(identity, "_proc_stat", lambda pid: {
        20: current, 1: {"pid": 1, "ppid": 0, "start_ticks": 31}
    }[pid])
    assert helper._validate_receipt(good, "a" * 32, 0)["relay_pid"] == 1
    with pytest.raises(ValueError, match="DISPOSABLE_NONCE_INVALID"):
        helper._validate_receipt(good, "b" * 32, 0)
    bad_chain = {**good, "ancestors": [current, {"pid": 30, "ppid": 0, "start_ticks": 31}]}
    with pytest.raises(ValueError, match="DISPOSABLE_ANCESTRY_INVALID"):
        helper._validate_receipt(bad_chain, "a" * 32, 0)


def test_expire_requires_actual_socket_disappearance(setup_helper, monkeypatch):
    environment, _, _, receipt_for, _, _ = setup_helper
    process_box = {}

    def popen(command, **kwargs):
        process = _Process(receipt_for(command[-1]), lambda: None)
        process_box["process"] = process
        return process

    monkeypatch.setattr(helper.subprocess, "Popen", popen)
    monkeypatch.setattr(helper, "SOCKET_EXPIRY_TIMEOUT_SECONDS", 0)
    row = {}
    with pytest.raises(ValueError, match="DISPOSABLE_SOCKET_NOT_EXPIRED"):
        with helper.disposable_session(environment, row) as session:
            session.expire()
    assert row["normal_exit_completed"]
    assert not row["actual_socket_expired"]


def test_primary_body_error_survives_cleanup_failure(setup_helper, monkeypatch):
    environment, infos, child_path, receipt_for, _, _ = setup_helper

    def popen(command, **kwargs):
        return _Process(receipt_for(command[-1]), lambda: infos.pop(str(child_path), None))

    monkeypatch.setattr(helper.subprocess, "Popen", popen)
    row = {}
    with pytest.raises(RuntimeError, match="primary") as raised:
        with helper.disposable_session(environment, row):
            infos.pop(environment["WSL_INTEROP"], None)
            raise RuntimeError("primary")
    assert row["cleanup_reason"] == "DISPOSABLE_SOCKET_EXPIRED"
    assert any("DISPOSABLE_CLEANUP_FAILED" in note for note in raised.value.__notes__)


def test_disconnected_ancestry_is_refused(monkeypatch):
    child = {"pid": 20, "ppid": 30, "start_ticks": 10}
    unrelated = {"pid": 31, "ppid": 0, "start_ticks": 11}
    monkeypatch.setattr(identity, "_proc_stat", lambda pid: {20: child, 31: unrelated}[pid])
    receipt = {
        "nonce": "a" * 32, "uid": 0, "pid": 20, "ppid": 30,
        "socket": {"path": "/run/WSL/31_interop", "inode": 4, "uid": 0},
        "ancestors": [child, unrelated],
    }
    with pytest.raises(ValueError, match="DISPOSABLE_ANCESTRY_INVALID"):
        helper._validate_receipt(receipt, "a" * 32, 0)


def test_wrong_socket_basename_pid_is_refused(monkeypatch):
    child = {"pid": 20, "ppid": 1, "start_ticks": 10}
    monkeypatch.setattr(identity, "_proc_stat", lambda pid: child)
    receipt = {
        "nonce": "a" * 32, "uid": 0, "pid": 20, "ppid": 1,
        "socket": {"path": "/run/WSL/99_interop", "inode": 4, "uid": 0},
        "ancestors": [child],
    }
    with pytest.raises(ValueError, match="DISPOSABLE_SOCKET_ANCESTRY_INVALID"):
        helper._validate_receipt(receipt, "a" * 32, 0)


def test_forbidden_socket_path_is_rejected_before_lstat(monkeypatch):
    monkeypatch.setattr(helper.Path, "lstat", lambda path: (_ for _ in ()).throw(AssertionError))
    with pytest.raises(ValueError, match="DISPOSABLE_SOCKET_INVALID"):
        identity.socket_identity({"WSL_INTEROP": "/tmp/not-an-interop-socket"})


def test_resolved_mounted_root_is_refused(tmp_path, monkeypatch):
    mounted = tmp_path / "mounted-root"
    mounted.symlink_to("/mnt")
    monkeypatch.setattr(helper, "ROOT", mounted)
    with pytest.raises(ValueError, match="DISPOSABLE_NATIVE_ROOT_REQUIRED"):
        helper._launch_values({"WSL_DISTRO_NAME": "Ubuntu"})


def test_child_only_reads_bridge_environment_keys(monkeypatch):
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    captured = {}
    output = io.BytesIO()
    monkeypatch.setattr(helper.sys, "stdin", SimpleNamespace(fileno=lambda: read_fd))
    monkeypatch.setattr(helper.sys, "stdout", SimpleNamespace(buffer=output))

    def fake_socket(environment):
        captured.update(environment)
        return {"path": "/run/WSL/1_interop", "inode": 4, "uid": os.getuid(), "_dev": 1}

    monkeypatch.setattr(helper, "_socket_stat", fake_socket)
    monkeypatch.setattr(helper, "_child_ancestors", lambda: [{
        "pid": os.getpid(), "ppid": os.getppid(), "start_ticks": 1,
    }])
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/1_interop")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-read")
    try:
        assert helper._child_main(["--child", "a" * 32]) == 0
    finally:
        os.close(read_fd)
    assert captured == {"WSL_INTEROP": "/run/WSL/1_interop", "WSL_DISTRO_NAME": "Ubuntu"}
