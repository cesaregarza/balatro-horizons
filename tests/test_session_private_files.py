"""Shared private-file safeguards for credentials and Windows registration."""

import json
import stat
from types import SimpleNamespace

import pytest

from balatro_horizons.cli import credentials, workbench_session
from balatro_horizons.game import windows_context as context


@pytest.fixture
def registration(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "ROOT", tmp_path)
    monkeypatch.setattr(context, "require_socket", lambda environment: None)
    return {
        "WSL_INTEROP": "/run/WSL/123_interop",
        "USERPROFILE": "/profile",
        "APPDATA": "/appdata",
        "LOCALAPPDATA": "/localappdata",
    }


def test_session_refuses_private_directory_symlink(registration, tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    (tmp_path / "private").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="SYMLINK_DESTINATION_FORBIDDEN"):
        context.register_session(registration)
    assert list(target.iterdir()) == []


def test_session_refuses_registration_symlink(registration, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    target = tmp_path / "untouched.json"
    target.write_bytes(b"untouched")
    (private / "windows-session.json").symlink_to(target)

    with pytest.raises(ValueError, match="SYMLINK_DESTINATION_FORBIDDEN"):
        context.register_session(registration)
    assert target.read_bytes() == b"untouched"


def test_session_refuses_symlinked_ancestor_without_touching_target(
    registration, tmp_path, monkeypatch
):
    outside = tmp_path / "outside"
    private = outside / "repo" / "private"
    private.mkdir(parents=True)
    private.chmod(0o755)
    marker = private / "marker"
    marker.write_bytes(b"untouched")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(context, "ROOT", linked_root / "repo")

    with pytest.raises(ValueError, match="SYMLINK_DESTINATION_FORBIDDEN"):
        context.register_session(registration)
    assert marker.read_bytes() == b"untouched"
    assert stat.S_IMODE(private.stat().st_mode) == 0o755


def test_session_refuses_non_directory_parent(registration, tmp_path):
    (tmp_path / "private").write_text("untouched")

    with pytest.raises(ValueError, match="CREDENTIAL_DESTINATION_UNSAFE"):
        context.register_session(registration)


def test_session_refuses_nonregular_registration_destination(registration, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    destination = private / "windows-session.json"
    destination.mkdir()
    before = stat.S_IMODE(destination.stat().st_mode)

    with pytest.raises(ValueError, match="CREDENTIAL_DESTINATION_UNSAFE"):
        context.register_session(registration)
    assert destination.is_dir()
    assert stat.S_IMODE(destination.stat().st_mode) == before


def test_session_mkdir_is_pinned_to_private_mode(registration, tmp_path, monkeypatch):
    original_mkdir = context.Path.mkdir
    modes = []

    def capture_mkdir(path, *args, **kwargs):
        if path == tmp_path / "private":
            modes.append(kwargs.get("mode"))
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(context.Path, "mkdir", capture_mkdir)
    context.register_session(registration)
    assert modes == [0o700]


def test_session_creates_private_parent_and_exact_sorted_registration(
    registration, tmp_path
):
    context.register_session(registration)
    path = context.context_path()
    expected = json.dumps(
        {"version": 1, "environment": context.session_environment(registration)},
        sort_keys=True,
    ).encode()
    assert path.read_bytes() == expected
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_session_check_and_bridge_keep_registered_environment(registration):
    context.register_session(registration)

    assert context.connection_status() == {
        "ready": True,
        "code": None,
        "message": "Windows connection registered; game startup is verified when a run starts.",
    }
    bridge = context.bridge_environment()
    assert {key: bridge[key] for key in registration} == registration
    assert "OPENAI_API_KEY" not in bridge


def test_credentials_cli_error_is_named_and_does_not_echo_private_data(tmp_path, capsys):
    root = tmp_path / "private-value-root"
    units = tmp_path / "units"
    source = tmp_path / "source.env"
    source.write_text("OPENAI_API_KEY=secret-value\n")
    units.mkdir()
    (units / "balatro-horizons.service.d").write_text("not a directory")
    args = SimpleNamespace(root=root, unit_dir=units, source=source, apply=True)

    assert credentials.run(args) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"error": "CREDENTIAL_DESTINATION_UNSAFE"}
    assert "secret-value" not in output and str(root) not in output


def test_session_cli_sanitizes_private_destination_refusal(
    registration, tmp_path, monkeypatch, capsys
):
    private = tmp_path / "private"
    private.mkdir()
    (private / "windows-session.json").mkdir()
    monkeypatch.setattr(workbench_session, "ROOT", tmp_path)
    monkeypatch.setattr(workbench_session, "session_environment", lambda source: registration)
    monkeypatch.setattr(workbench_session, "require_socket", lambda environment: None)

    with pytest.raises(SystemExit, match="WINDOWS_SESSION_REGISTRATION_FAILED"):
        workbench_session.run(SimpleNamespace(apply=True, check=False))
    output = capsys.readouterr().out
    assert str(tmp_path) not in output


@pytest.mark.parametrize("linked", ["private", "windows-session.json", "native-worker.lock"])
def test_session_cli_refuses_links_before_creating_lock(
    registration, tmp_path, monkeypatch, capsys, linked
):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    marker = outside / "marker"
    marker.write_bytes(b"untouched")
    private = tmp_path / "private"
    if linked == "private":
        private.symlink_to(outside, target_is_directory=True)
    else:
        private.mkdir()
        (private / linked).symlink_to(marker)
    before_mode = stat.S_IMODE(outside.stat().st_mode)
    monkeypatch.setattr(workbench_session, "ROOT", tmp_path)
    monkeypatch.setattr(workbench_session, "session_environment", lambda source: registration)
    monkeypatch.setattr(workbench_session, "require_socket", lambda environment: None)

    with pytest.raises(SystemExit) as error:
        workbench_session.run(SimpleNamespace(apply=True, check=False))
    assert str(error.value) == "WINDOWS_SESSION_REGISTRATION_FAILED"
    assert str(tmp_path) not in capsys.readouterr().out
    assert list(outside.iterdir()) == [marker]
    assert marker.read_bytes() == b"untouched"
    assert stat.S_IMODE(outside.stat().st_mode) == before_mode
    if linked != "windows-session.json":
        assert not (private / "windows-session.json").exists()
    if linked != "native-worker.lock":
        assert not (private / "native-worker.lock").exists()
