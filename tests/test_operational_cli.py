import json
from unittest.mock import Mock

import pytest

from balatro_horizons.cli import main
from balatro_horizons.cli import operations as modules
from balatro_horizons.cli.parser import build_parser
from balatro_horizons.config import ROOT

COMMANDS = [
    ("offline", modules.offline, []),
    ("deploy frontend", modules.deploy_frontend, ["--build", "b", "--dist", "d", "--backup", "s"]),
    ("deploy candidate", modules.install_candidate, ["--root", "r"]),
    ("guide package", modules.package_balatro_guide, ["--guide", "g"]),
    ("prompt sync", modules.prompt_sync, []),
    ("human register", modules.register_player, [
        "--alias", "a", "--clone", "b", "--model", "m", "--input-rate", "1",
        "--output-rate", "2", "--pricing-date", "2026-09-21",
    ]),
    ("smoke", modules.smoke, []),
    ("native diagnose", modules.diagnose_native, []),
    ("review session", modules.workbench_session, []),
    ("review status", modules.workbench_status, []),
]


@pytest.mark.parametrize("command,module,required", COMMANDS)
def test_command_selects_its_package_handler_and_preserves_exit_code(
    command, module, required, monkeypatch, capsys,
):
    handler = Mock(return_value=7)
    monkeypatch.setattr(module, "run", handler)
    assert main([*command.split(), *required]) == 7
    handler.assert_called_once()
    assert handler.call_args.args[0].operation_parser.prog == "bh " + command
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("command,module,required", COMMANDS)
def test_help_exits_before_any_operational_action(command, module, required, monkeypatch, capsys):
    handler = Mock(side_effect=AssertionError("help must not execute"))
    monkeypatch.setattr(module, "run", handler)
    with pytest.raises(SystemExit) as stopped:
        main([*command.split(), "--help"])
    assert stopped.value.code == 0
    assert "usage: bh " + command in capsys.readouterr().out
    handler.assert_not_called()


def test_existing_review_human_and_native_commands_keep_their_defaults():
    parser = build_parser()
    review = parser.parse_args(["review"])
    assert (review.host, review.port, review.workbench) == ("127.0.0.1", 8765, False)
    human = parser.parse_args(["human"])
    assert human.action_file is None
    for args in [review, human, *(parser.parse_args(["native", op]) for op in ("launch", "stop", "status"))]:
        assert not hasattr(args, "operation_handler")
    assert parser.parse_args(["native", "stop"]).operation == "stop"


def test_new_default_paths_remain_bound_to_the_checkout():
    parser = build_parser()
    assert parser.parse_args(["offline"]).root == ROOT
    assert parser.parse_args(["prompt", "sync"]).root == ROOT
    assert parser.parse_args(["native", "diagnose"]).config == ROOT / "configs/pilot.yaml"
    smoke = parser.parse_args(["smoke"])
    assert smoke.config == ROOT / "configs/luna-smoke.yaml"
    assert (smoke.agent, smoke.campaign) == ("luna", "openai-luna-smoke")
    assert (smoke.authorized_episode_cap, smoke.authorized_total_cap) == (1, 5)
    assert not smoke.allow_paid and not smoke.transport_only


@pytest.mark.parametrize("command", ["deploy", "guide", "prompt", "native"])
def test_required_subcommands_remain_required(command):
    with pytest.raises(SystemExit) as stopped:
        main([command])
    assert stopped.value.code == 2


def test_native_diagnostic_modes_are_exclusive_before_launch(monkeypatch):
    bridge = Mock()
    monkeypatch.setattr(modules.diagnose_native, "WindowsBridge", bridge)
    with pytest.raises(SystemExit) as stopped:
        main(["native", "diagnose", "--restart", "--workbench"])
    assert stopped.value.code == 2
    bridge.assert_not_called()


@pytest.mark.parametrize("running,episode,expected", [(False, None, 0), (True, None, 2), (False, "active", 2)])
def test_review_status_retains_idle_gate_and_allowlisted_output(
    running, episode, expected, monkeypatch, capsys,
):
    request = Mock(return_value={"running": running, "active_episode": episode, "error": None, "private": "omitted"})
    monkeypatch.setattr(modules.workbench_status, "operator_request", request)
    monkeypatch.setattr(modules.workbench_status, "perf_counter", Mock(side_effect=[1, 1.125]))
    assert main(["review", "status", "--fail-if-running", "--timing"]) == expected
    assert json.loads(capsys.readouterr().out) == {
        "running": running, "active_episode": episode, "error": None, "response_ms": 125,
    }
    request.assert_called_once_with("/operator/status")


def test_review_status_unavailable_retains_sanitized_error(monkeypatch, capsys):
    request = Mock(side_effect=ValueError("private-value"))
    monkeypatch.setattr(modules.workbench_status, "operator_request", request)
    assert main(["review", "status"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "WORKBENCH_STATUS_UNAVAILABLE"}


def test_review_session_preview_does_not_restart_service(monkeypatch, capsys):
    monkeypatch.setattr(modules.workbench_session.os, "environ", {
        "WSL_INTEROP": "/test/socket", "USERPROFILE": "/test/profile",
        "APPDATA": "/test/appdata", "LOCALAPPDATA": "/test/local", "OPENAI_API_KEY": "secret",
    })
    restart = Mock()
    monkeypatch.setattr(modules.workbench_session.subprocess, "run", restart)
    assert main(["review", "session"]) is None
    output = capsys.readouterr().out
    assert "Preview only" in output and "secret" not in output and "OPENAI_API_KEY" not in output
    restart.assert_not_called()


def test_prompt_sync_command_keeps_check_only_failure_and_explicit_write(tmp_path, capsys):
    prompts = tmp_path / "configs/prompts"
    prompts.mkdir(parents=True)
    (prompts / "ALWAYS-LOADED.md").write_text("new instructions\n")
    target = prompts / "harness.txt"
    original = f"prefix\n{modules.prompt_sync.BEGIN}\nold\n{modules.prompt_sync.END}\nsuffix\n"
    target.write_text(original)
    with pytest.raises(SystemExit) as stopped:
        main(["prompt", "sync", "--root", str(tmp_path)])
    assert stopped.value.code == 1 and "stale" in capsys.readouterr().err
    assert target.read_text() == original
    assert main(["prompt", "sync", "--root", str(tmp_path), "--write"]) == 0
    assert modules.prompt_sync.sync(tmp_path)
    assert target.read_text().startswith("prefix\n") and target.read_text().endswith("suffix\n")
