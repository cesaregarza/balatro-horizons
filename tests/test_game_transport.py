"""Offline checks for the game transport boundary."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from balatro_horizons.game import transport
from balatro_horizons.game.contract import RPC_METHODS, NativeFailure, NativeRejected
from balatro_horizons.game.environment import Environment, windows_runtime_path
from balatro_horizons.game.session import NativeGame


def test_windows_runtime_path_is_pure_and_handles_wsl_drive():
    assert windows_runtime_path("/mnt/d/BalatroHorizonsRuntime") == r"D:\BalatroHorizonsRuntime"
    assert windows_runtime_path("X") == "X"


def test_environment_exposes_runtime_and_named_timeouts():
    environment = Environment(runtime="/mnt/e/runtime")
    assert environment.windows_runtime == r"E:\runtime"
    assert environment.launch_timeout_seconds == 45
    assert environment.http_timeout_seconds == 90
    assert environment.rpc_response_margin_seconds == 5


def test_launch_command_uses_environment_runtime():
    bridge = transport.WindowsBridge(Environment(runtime="X"))
    command = bridge._command("launch")
    assert "X\\bridge.ps1" in command
    assert command[command.index("-Runtime") + 1] == "X"


def test_eio_process_spawn_retries_three_times(monkeypatch):
    spawn = Mock(side_effect=[OSError(5, "EIO"), Mock()])
    monkeypatch.setattr(transport.subprocess, "Popen", spawn)
    monkeypatch.setattr(transport.time, "sleep", Mock())
    transport.WindowsBridge._spawn_with_retry(["powershell"])
    assert spawn.call_count == 2


@pytest.mark.parametrize(
    "code,exception,name",
    [
        ("UNAFFORDABLE", NativeRejected, "NOT_ALLOWED"),
        ("BUSY", NativeFailure, "INFRASTRUCTURE"),
        ("METHOD_FORBIDDEN", NativeFailure, "HARNESS_FAULT"),
        ("UNKNOWN_LUA_CODE", NativeFailure, "INFRASTRUCTURE"),
    ],
)
def test_rpc_error_taxonomy_does_not_leak_raw_text(code, exception, name):
    with pytest.raises(exception) as raised:
        transport.raise_rpc_error(
            {"message": code, "data": {"name": name, "raw": "secret"}}
        )
    assert raised.value.code == code
    assert raised.value.name == name
    assert "secret" not in str(raised.value)


def test_known_code_with_wrong_name_fails_as_infrastructure():
    with pytest.raises(NativeFailure) as raised:
        transport.raise_rpc_error(
            {"message": "BUSY", "data": {"name": "NOT_ALLOWED"}}
        )
    assert raised.value.code == "BUSY"
    assert raised.value.name == "NOT_ALLOWED"


def test_rpc_error_with_unvalidated_text_uses_safe_code():
    with pytest.raises(NativeFailure) as raised:
        transport.raise_rpc_error({"message": "raw secret"})
    assert raised.value.code == "RPC_ENDPOINT_FAILURE"
    assert raised.value.raw_message == "raw secret"
    assert "raw secret" not in str(raised.value)


@pytest.mark.parametrize("name", ["BAD_REQUEST", "INVALID_STATE", "NOT_ALLOWED"])
def test_upstream_rejection_uses_generic_public_code_and_keeps_raw_message(name):
    with pytest.raises(NativeRejected) as raised:
        transport.raise_rpc_error({"data": {"name": name, "message": "INVALID_BLIND"}})
    assert raised.value.code == "NATIVE_ACTION_REJECTED"
    assert raised.value.name == name
    assert raised.value.raw_message == "INVALID_BLIND"
    assert "INVALID_BLIND" not in str(raised.value)


def test_non_token_upstream_not_allowed_is_rejected_generically():
    with pytest.raises(NativeRejected) as raised:
        transport.raise_rpc_error({"data": {"name": "NOT_ALLOWED", "message": "invalid blind"}})
    assert raised.value.code == "NATIVE_ACTION_REJECTED"


def test_known_infrastructure_code_cannot_be_reclassified_by_a_nested_upstream_name():
    with pytest.raises(NativeFailure) as raised:
        transport.raise_rpc_error({"data": {"name": "NOT_ALLOWED", "message": "BUSY"}})
    assert raised.value.code == "BUSY"


def test_unknown_token_claiming_not_allowed_is_infrastructure():
    with pytest.raises(NativeFailure) as raised:
        transport.raise_rpc_error({"message": "ANYTHING", "name": "NOT_ALLOWED"})
    assert raised.value.code == "ANYTHING"


def test_rpc_reader_keeps_margin_beyond_powershell_timeout(monkeypatch):
    bridge = transport.WindowsBridge(Environment(http_timeout_seconds=90))
    bridge._rpc_process = Mock()
    bridge._rpc_process.poll.return_value = None
    selector = MagicMock()
    selector.__enter__.return_value = selector
    selector.select.return_value = []
    monkeypatch.setattr(transport.selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(transport.time, "monotonic", Mock(side_effect=[100, 100]))
    with pytest.raises(NativeFailure, match="WINDOWS_BRIDGE_TIMEOUT"):
        bridge._exchange("{}\n")
    selector.select.assert_called_once_with(95)


@pytest.mark.parametrize(
    "code,name,exception",
    [("BUSY", "INFRASTRUCTURE", NativeFailure),
     ("UNAFFORDABLE", "NOT_ALLOWED", NativeRejected)],
)
def test_reconciled_rejection_uses_the_same_taxonomy(code, name, exception):
    game = NativeGame.__new__(NativeGame)
    game._closed = False
    game.raw = {}
    game.bridge = Mock()
    game.bridge.rpc.side_effect = [
        NativeFailure("LOST_ACK"),
        {"status": "rejected", "response": {"message": code, "name": name}},
    ]
    game.wait_ready = Mock()
    with pytest.raises(exception) as raised:
        game.apply_public_action(SimpleNamespace(type="select_blind"), None, "request-1")
    assert raised.value.code == code
    assert raised.value.name == name


@pytest.mark.parametrize("response", [None, {}, {"message": "raw secret"}])
def test_reconciled_rejection_without_valid_envelope_is_infrastructure(response):
    game = NativeGame.__new__(NativeGame)
    game._closed = False
    game.raw = {}
    game.bridge = Mock()
    game.bridge.rpc.side_effect = [
        NativeFailure("LOST_ACK"),
        {"status": "rejected", "response": response},
    ]
    game.wait_ready = Mock()
    with pytest.raises(NativeFailure) as raised:
        game.apply_public_action(SimpleNamespace(type="select_blind"), None, "request-1")
    assert raised.value.code == "RPC_ENDPOINT_FAILURE"


def test_closed_native_game_cannot_start_another_run():
    game = NativeGame.__new__(NativeGame)
    game._closed = True
    game.bridge = Mock()
    with pytest.raises(NativeFailure, match="NATIVE_GAME_CLOSED"):
        game.start_run("RED", "GOLD", "SEED")
    game.bridge.rpc.assert_not_called()


def test_power_shell_allowlist_matches_contract():
    text = Path(__file__).parents[1].joinpath("native/bridge.ps1").read_text()
    start = text.index("$allowed = @(")
    values = text[start:].split(")", 1)[0].split("@(", 1)[1]
    allowlist = {item.strip().strip("'") for item in values.split(",")}
    assert allowlist == set(RPC_METHODS)
    assert len(allowlist) == 15
