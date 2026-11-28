"""The worker's stages on a fake machine: no root, apt, network, display or game.

Each test runs the worker against a temporary layout. The fake machine records every
command, simulates what the commands would leave on disk, serves the pinned
downloads from memory, and answers the game's API in process, as a test scripts it or
as the fake local API plays one short game. It lives in `worker_support`.
"""

import ast
import errno
import hashlib
import http.client
import io
import json
import shlex
import stat
import sys
import tomllib
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest
from worker_support import (
    BUILD,
    BUNDLE,
    PACKAGE,
    PINS,
    START,
    Clock,
    FakeMachine,
    job,
    layout_in,
    run,
)

from balatro_horizons.cloud import install
from balatro_horizons.cloud.access import Bundle
from balatro_horizons.cloud.ledger import WorkerStatus
from balatro_horizons.cloud.worker import worker


@pytest.fixture
def layout(tmp_path):
    return layout_in(tmp_path)


@pytest.fixture
def machine(layout):
    return FakeMachine(layout, Clock())


def test_stages_run_in_order_each_written_before_its_commands(machine, layout):
    code, status, _ = run(machine, layout)
    assert (code, status["state"], status["stage"], status["reason"]) == (
        0, "finished", "play", None)
    first = {}
    for command, stage in machine.stages:
        first.setdefault(stage, command)
    # The ready and play stages run no command of their own; play's first is the final
    # observation.
    assert first == {"packages": "cloud-init", "dependencies": "apt-get", "bundle": "tar",
                     "user": "id", "display": "systemd-run", "game": "systemd-run",
                     "play": "dpkg-query"}
    assert set(status["started_at"]) == set(first) | {"ready"}
    assert machine.posts, "readiness is checked over the game's API"
    assert stat.S_IMODE(layout.status.stat().st_mode) == 0o600
    assert (status.get("outcome"), status.get("actions")) == ("game_over", 5)


def test_play_follows_ready_and_the_status_is_written_after_each_committed_move(machine,
                                                                               layout):
    _, status, _ = run(machine, layout)
    methods = [body["method"] for _, body, _ in machine.posts]
    starts = [index for index, method in enumerate(methods) if method == "start"]
    # Ready ends on an inspect that finds the menu; play starts the game next.
    assert len(starts) == 1 and methods[:starts[0]] and set(methods[:starts[0]]) == {
        "bh_inspect"}
    committed = 0
    for method, count in zip(methods, machine.counts_at_post, strict=True):
        assert count == committed
        committed += method not in ("bh_inspect", "bh_request_status")
    assert committed == status.get("actions") == 5


def test_packages_follow_the_pilot_order(machine, layout):
    run(machine, layout)
    commands = [c for c in machine.commands if c[0] in ("cloud-init", "dpkg", "apt-get")]
    assert commands[:5] == [
        ["cloud-init", "status", "--wait"], ["cloud-init", "status", "--long"],
        ["dpkg", "--add-architecture", "i386"],
        ["apt-get", "-o", "DPkg::Lock::Timeout=600", "update"],
        ["apt-get", "-o", "DPkg::Lock::Timeout=600", "install", "-y", "xvfb", "python3"]]


def test_the_seed_is_observed_before_play_and_starts_the_game(machine, layout):
    _, status, _ = run(machine, layout)
    seed = status["observed"].get("seed")
    assert isinstance(seed, str) and len(seed) == 8
    assert set(seed) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    starts = [body["params"] for _, body, _ in machine.posts if body["method"] == "start"]
    assert [(p["seed"], p["deck"], p["stake"]) for p in starts] == [(seed, "RED", "WHITE")]


def test_the_results_are_written_owner_only_before_the_finished_status(machine, layout,
                                                                      monkeypatch):
    written = []
    write = worker.write_private

    def recording(path, text):
        if path == layout.status:
            written.append(("status", json.loads(text)["state"]))
        else:
            written.append((path.name, None))
        write(path, text)

    monkeypatch.setattr(worker, "write_private", recording)
    code, status, runner = run(machine, layout)
    assert (code, status["state"]) == (0, "finished")
    results_path = layout.worker / "results.json"
    # The status says finished only once, after the results.
    assert written.count(("status", "finished")) == 1
    assert written[-2:] == [("results.json", None), ("status", "finished")]
    assert stat.S_IMODE(results_path.stat().st_mode) == 0o600
    text = results_path.read_text()
    results = json.loads(text)
    assert set(results) == {"outcome", "actions", "ante", "round", "seed", "deck", "stake",
                            "moves", "started_at", "observed"}
    assert (results["outcome"], results["actions"], results["ante"], results["round"]) == (
        "game_over", 5, 1, 1)
    assert (results["seed"], results["deck"], results["stake"]) == (
        status["observed"]["seed"], "RED", "WHITE")
    assert results["moves"][0] == {"method": "start", "action": None, "state": "BLIND_SELECT"}
    assert [move["action"] for move in results["moves"]][2:] == [
        "discard", "discard", "play_hand"]
    assert (results["started_at"], results["observed"]) == (
        status["started_at"], status["observed"])
    request_ids = [body["id"] for _, body, _ in machine.posts]
    for secret in (runner.token, runner.instance_id, *request_ids):
        assert secret not in text


def test_results_that_cannot_be_written_fail_the_play_stage(machine, layout, monkeypatch):
    write = worker.write_private

    def no_space_for_the_results(path, text):
        if path == layout.results:
            raise OSError(errno.ENOSPC, "No space left on device")
        write(path, text)

    monkeypatch.setattr(worker, "write_private", no_space_for_the_results)
    runner = worker.Worker(machine, layout, machine.clock)
    try:
        code = runner.run(job(), PINS)
    except Exception as error:
        # An error that escapes is the outcome, so the assertion below names it.
        code = type(error).__name__
    status = json.loads(layout.status.read_text())
    assert (code, status["state"], status["stage"], status["reason"]) == (
        1, "failed", "play", "results not written: ENOSPC")
    assert set(status["evidence"]) == {"display_stderr", "game_stderr"}
    assert not layout.results.exists()
    WorkerStatus.model_validate(status)


def fault_at(machine, fault, call=("bh_action", 1)):
    """Script a fault on the game's local API for one call, once the game has started."""
    start = machine.start

    def starting(argv):
        start(argv)
        if machine.local_api is not None:
            machine.local_api.faults = {call: fault}

    machine.start = starting


def test_a_refused_move_fails_play_and_its_results_are_written_first(machine, layout,
                                                                    monkeypatch):
    written = []
    write = worker.write_private

    def recording(path, text):
        written.append(("status", json.loads(text)["state"]) if path == layout.status
                       else (path.name, None))
        write(path, text)

    monkeypatch.setattr(worker, "write_private", recording)
    fault_at(machine, "refuse")
    code, status, runner = run(machine, layout)
    assert (code, status["state"], status["stage"], status["reason"]) == (
        1, "failed", "play", "move refused: bh_action discard: BUSY")
    assert status.get("actions") == 2 and "game_stderr" in status["evidence"]
    # The moves so far come home with no outcome, written before the final status.
    assert written[-2:] == [("results.json", None), ("status", "failed")]
    text = layout.results.read_text()
    results = json.loads(text)
    assert (results["outcome"], results["actions"]) == (None, 2)
    assert [move["method"] for move in results["moves"]] == ["start", "select"]
    assert (results["started_at"], results["observed"]) == (
        status["started_at"], status["observed"])
    assert stat.S_IMODE(layout.results.stat().st_mode) == 0o600
    request_ids = [body["id"] for _, body, _ in machine.posts]
    for secret in (runner.token, runner.instance_id, *request_ids):
        assert secret not in text


def test_a_failed_play_keeps_its_own_reason_when_its_results_cannot_be_written(machine, layout,
                                                                               monkeypatch):
    attempts = []
    write = worker.write_private

    def no_space_for_the_results(path, text):
        if path == layout.results:
            attempts.append(path.name)
            raise OSError(errno.ENOSPC, "No space left on device")
        write(path, text)

    monkeypatch.setattr(worker, "write_private", no_space_for_the_results)
    fault_at(machine, "refuse")
    code, status, _ = run(machine, layout)
    assert attempts == ["results.json"]
    assert (code, status["state"], status["stage"], status["reason"]) == (
        1, "failed", "play", "move refused: bh_action discard: BUSY")
    assert not layout.results.exists()
    WorkerStatus.model_validate(status)


def test_the_game_exiting_during_play_fails_the_play_stage(machine, layout):
    post = machine.post

    def exits_after_two_moves(url, body, timeout):
        reply = post(url, body, timeout)
        if machine.local_api.committed >= 2:
            machine.units["bh-game"] = False
        return reply

    machine.post = exits_after_two_moves
    code, status, _ = run(machine, layout)
    assert (code, status["stage"], status["reason"]) == (1, "play", "game exited")


# The message IDs systemd gives its records about a unit.
OOM_KILLED = "fe6faa94e7774663a0da52717891d8ef"
UNIT_FAILED = "d9b373ed55a64feb8242e02dbe79a49c"
UNIT_SUCCEEDED = "7ad2d189f7e94e70a38c781354912448"
PROCESS_EXITED = "98e322203f7a4ed290d09fe03c09fe15"
# Free text, standing in for what a journal record or a cgroup file could hold.
RAW = "raw text that never reaches a status"


def game_cgroup(layout, files):
    cgroup = layout.cgroup_root / "system.slice/bh-game.service"
    cgroup.mkdir(parents=True)
    for name, text in files.items():
        (cgroup / name).write_text(text)


def test_a_game_killed_for_memory_during_a_move_reports_how_its_unit_ended(machine, layout):
    # The stopped unit's records, with only the fields the worker asks the journal for, and
    # the free text a record could carry.
    machine.journal = [
        {"__CURSOR": "s=1", "MESSAGE_ID": OOM_KILLED, "MESSAGE": RAW},
        {"__CURSOR": "s=2", "MESSAGE_ID": PROCESS_EXITED, "EXIT_CODE": "killed",
         "EXIT_STATUS": "9", "MESSAGE": RAW},
        {"__CURSOR": "s=3", "MESSAGE_ID": UNIT_FAILED, "UNIT_RESULT": "oom-kill", "MESSAGE": RAW},
    ]
    fault_at(machine, "exit")
    code, status, _ = run(machine, layout)
    assert (code, status["stage"], status["reason"]) == (1, "play", (
        "game exited during bh_action discard; answer RemoteDisconnected after 0 s; "
        "status lookup URLError"))
    assert status["observed"].get("game_unit") == (
        "running=no main=killed/9 result=oom-kill journal_oom=yes")
    assert RAW not in json.dumps(status) and RAW not in layout.results.read_text()
    WorkerStatus.model_validate(status)


def test_a_game_that_exits_cleanly_during_a_move_reports_its_unit_succeeded(machine, layout):
    machine.journal = [
        {"__CURSOR": "s=1", "MESSAGE_ID": PROCESS_EXITED, "EXIT_CODE": "exited",
         "EXIT_STATUS": "0"},
        {"__CURSOR": "s=2", "MESSAGE_ID": UNIT_SUCCEEDED},
    ]
    fault_at(machine, "exit")
    _, status, _ = run(machine, layout)
    assert status["observed"].get("game_unit") == (
        "running=no main=exited/0 result=success journal_oom=no")


def test_a_game_still_running_at_a_failure_reports_its_memory(machine, layout):
    game_cgroup(layout, {"memory.peak": "5134123008\n", "memory.current": "4831838208\n",
                         "memory.events": f"low 0\nhigh 0\nmax 2\noom 1\noom_kill 1\n{RAW} 7\n"})
    machine.journal = [{"__CURSOR": "s=1", "MESSAGE_ID": OOM_KILLED, "MESSAGE": RAW}]
    fault_at(machine, "refuse")
    _, status, _ = run(machine, layout)
    assert status["observed"].get("game_unit") == (
        "running=yes memory.peak=5134123008 memory.current=4831838208 oom=1 oom_kill=1 max=2 "
        "journal_oom=yes")
    assert RAW not in json.dumps(status)
    # Every read is bounded, and the journal is asked for the parsed fields alone.
    reads = [(command, timeout) for command, timeout in zip(machine.commands, machine.timeouts,
                                                            strict=True)
             if command[0] in ("journalctl", "systemctl")]
    assert [command[0] for command, _ in reads] == ["journalctl", "systemctl"]
    assert all(timeout <= 30 for _, timeout in reads)
    journal = reads[0][0]
    assert "--output-fields=MESSAGE_ID,EXIT_CODE,EXIT_STATUS,UNIT_RESULT" in journal
    assert [option for option in journal if option.startswith("--lines=")] == ["--lines=200"]


def game_never_starts(machine):
    start = machine.start

    def fails_for_the_game(argv):
        if "--unit=bh-game" in argv:
            raise worker.StageFailed("systemd-run exit 1")
        start(argv)

    machine.start = fails_for_the_game


def unreadable_everywhere(machine, layout):
    """The game's unit never starts, and neither systemctl nor journalctl answers."""
    game_never_starts(machine)
    machine.unreadable = {"systemctl", "journalctl"}


def garbled_while_running(machine, layout):
    machine.journal = ["not json {"]
    machine.control_group = "not a control group"
    fault_at(machine, "refuse")


def odd_records_once_stopped(machine, layout):
    machine.journal = [{"EXIT_CODE": "exploded", "EXIT_STATUS": "9"},
                       {"EXIT_CODE": "killed", "EXIT_STATUS": "nine"},
                       {"UNIT_RESULT": "Out Of Memory!", "MESSAGE_ID": "0" * 32}]
    fault_at(machine, "exit")


def odd_counts_while_running(machine, layout):
    game_cgroup(layout, {"memory.peak": "a lot\n", "memory.events": "oom 0\nmax many\n"})
    fault_at(machine, "refuse")


@pytest.mark.parametrize(("breakage", "game_unit"), [
    (unreadable_everywhere,
     "running=unavailable main=unavailable result=unavailable journal_oom=unavailable"),
    (garbled_while_running,
     "running=yes memory.peak=unavailable memory.current=unavailable oom=unavailable "
     "oom_kill=unavailable max=unavailable journal_oom=unavailable"),
    (odd_records_once_stopped, "running=no main=unavailable result=unavailable journal_oom=no"),
    (odd_counts_while_running,
     "running=yes memory.peak=unavailable memory.current=unavailable oom=0 "
     "oom_kill=unavailable max=unavailable journal_oom=no"),
], ids=["unreadable", "garbled", "odd-records", "odd-counts"])
def test_a_value_the_worker_cannot_read_or_parse_is_unavailable(machine, layout, breakage,
                                                                game_unit):
    breakage(machine, layout)
    code, status, _ = run(machine, layout)
    assert (code, status["state"], status["observed"].get("game_unit")) == (1, "failed",
                                                                            game_unit)
    WorkerStatus.model_validate(status)


def fails_in(stage, machine):
    """Break the run at `stage`; play fails at its first move, before any has committed."""
    if stage == "display":
        machine.display_up = False
    elif stage == "game":
        game_never_starts(machine)
    elif stage == "ready":
        machine.reply = lambda body: {"jsonrpc": "2.0", "id": body["id"],
                                      "result": {"state": "SPLASH"}}
    else:
        fault_at(machine, "refuse", ("start", 1))


def test_only_a_failure_in_the_game_stages_reports_its_unit_and_only_moves_bring_results(
        tmp_path):
    reported = {}
    for stage in ("display", "game", "ready", "play"):
        layout = layout_in(tmp_path / stage)
        machine = FakeMachine(layout, Clock())
        fails_in(stage, machine)
        _, status, _ = run(machine, layout)
        reported[status["stage"]] = ("game_unit" in status["observed"], layout.results.exists())
    assert reported == {"display": (False, False), "game": (True, False), "ready": (True, False),
                        "play": (True, False)}


def test_ready_reports_what_the_run_observed(machine, layout):
    _, status, _ = run(machine, layout)
    observed = status["observed"]
    assert observed["game_version"] == "9.9.9-test"
    assert observed["steamrt4"] == f"steamrt4: {BUILD}"
    assert observed["cloud_init"] == "exit 0: status: done\nerrors: []\n"
    assert observed["renderer"].startswith("exit 0: OpenGL renderer string: llvmpipe")
    listing = "python3=3.12.3\nxvfb=2:21.1.12"
    assert observed["packages_sha256"] == hashlib.sha256(listing.encode()).hexdigest()
    assert observed["kernel"]


@pytest.mark.parametrize("data,reason", [(PACKAGE + b"!", "umu.deb size mismatch"),
                                         (PACKAGE[::-1], "umu.deb sha256 mismatch")])
def test_every_download_is_checked_before_anything_is_installed(machine, layout, data, reason):
    machine.files["https://downloads.invalid/umu.deb"] = data
    code, status, _ = run(machine, layout)
    assert (code, status["state"], status["stage"], status["reason"]) == (
        1, "failed", "dependencies", reason)
    installs = [c for c in machine.commands if c[0] == "apt-get" and "install" in c]
    assert installs == [["apt-get", "-o", "DPkg::Lock::Timeout=600", "install", "-y",
                         "xvfb", "python3"]]
    assert not [c for c in machine.commands if c[0] == "tar"]


@pytest.mark.parametrize("data,reason", [(BUNDLE + b"!", "bundle.tar size mismatch"),
                                         (BUNDLE[::-1], "bundle.tar sha256 mismatch")])
def test_the_bundle_is_checked_against_its_descriptor_before_it_is_unpacked(machine, layout,
                                                                           data, reason):
    (layout.worker / "bundle.tar").write_bytes(data)
    code, status, _ = run(machine, layout)
    assert (code, status["stage"], status["reason"]) == (1, "bundle", reason)
    assert not [c for c in machine.commands if c[0] == "tar" and "bundle.tar" in c[2]]


def test_a_bundle_nested_under_a_game_directory_is_refused(machine, layout):
    def nested(argv):
        destination = Path(argv[3].removeprefix("--directory="))
        if "--strip-components=1" in argv:
            (destination / "proton").write_text("stand-in")
            return
        (destination / "game").mkdir()
        (destination / "game" / worker.GAME_EXECUTABLE).write_text("stand-in")
        (destination / "game" / worker.MOD_DIRECTORY).mkdir()

    machine.extract = nested
    code, status, _ = run(machine, layout)
    assert (code, status["stage"], status["reason"]) == (
        1, "bundle", "bundle has no game executable")
    assert not [c for c in machine.commands if c[0] == "useradd"]


@pytest.mark.parametrize("missing,reason", [
    (worker.GAME_EXECUTABLE, "bundle has no game executable"),
    (worker.MOD_DIRECTORY, "bundle has no mod directory"),
])
def test_a_bundle_without_the_game_or_its_mods_fails_its_stage(machine, layout, missing,
                                                              reason):
    extract = machine.extract

    def incomplete(argv):
        extract(argv)
        destination = Path(argv[3].removeprefix("--directory="))
        if "--strip-components=1" not in argv:
            path = destination / missing
            path.rmdir() if path.is_dir() else path.unlink()

    machine.extract = incomplete
    _, status, _ = run(machine, layout)
    assert (status["stage"], status["reason"]) == ("bundle", reason)


def test_the_game_gets_only_the_launch_recipe_environment(machine, layout):
    _, _, runner = run(machine, layout)
    lines = [line for line in layout.env_file.read_text().split("\n") if line]
    environment = {name: value for name, _, value in (line.partition("=") for line in lines)}
    assert environment == {
        "BALATROBOT_ANIMATION_FPS": "'60'",
        "BALATROBOT_DEBUG": "'0'",
        "BALATROBOT_FAST": "'0'",
        "BALATROBOT_GAMESPEED": "'16'",
        "BALATROBOT_HEADLESS": "'0'",
        "BALATROBOT_HOST": "'127.0.0.1'",
        "BALATROBOT_PORT": "'12346'",
        "BALATROBOT_RENDER_ON_API": "'1'",
        "BH_CALIBRATION": "'0'",
        "BH_INSTANCE_ID": f"'{runner.instance_id}'",
        "BH_ISOLATED_RUNTIME": "'1'",
        "BH_RUNTIME": "'Z:" + str(layout.game).replace("/", "\\") + "'",
        "BH_TOKEN": f"'{runner.token}'",
        "DISPLAY": "':99'",
        "GALLIUM_DRIVER": "'llvmpipe'",
        "GAMEID": "'umu-default'",
        "HOME": f"'{layout.home}'",
        "LIBGL_ALWAYS_SOFTWARE": "'1'",
        "LOVELY_MOD_DIR": "'Z:" + str(layout.game / "Mods").replace("/", "\\") + "'",
        "LP_NUM_THREADS": "'2'",
        "PROTONPATH": f"'{layout.runners / 'GE-Proton11-14-x86_64'}'",
        "PROTON_LOG_DIR": f"'{layout.evidence}'",
        "SDL_AUDIODRIVER": "'dummy'",
        "STORE": "'none'",
        "WINEDLLOVERRIDES": "'version=n,b'",
        "WINEPREFIX": f"'{layout.prefix}'",
    }
    assert "PROTON_LOG" not in environment
    assert stat.S_IMODE(layout.env_file.stat().st_mode) == 0o600
    assert len(runner.token) == 64 and len(runner.instance_id) == 32
    assert all(runner.token not in " ".join(c) for c in machine.commands)


def test_the_game_runs_from_its_directory_with_the_pilot_limits(machine, layout):
    run(machine, layout)
    game = machine.started("bh-game")
    assert worker.GAME_EXECUTABLE == "Balatro.exe"
    assert game[-2:] == ["umu-run", str(layout.game / worker.GAME_EXECUTABLE)]
    properties = {a.removeprefix("--property=") for a in game if a.startswith("--property=")}
    assert {"User=player", f"WorkingDirectory={layout.game}", "CPUQuota=200%",
            "MemoryMax=6656M", "UMask=0077", f"EnvironmentFile={layout.env_file}",
            f"StandardError=file:{layout.evidence / 'game.err'}"} <= properties
    assert not {"MemoryMax=3328M", "MemoryMax=6G"} & properties
    display = machine.started("bh-display")
    assert display[-7:] == ["Xvfb", ":99", "-screen", "0", "1280x720x24", "-nolisten", "tcp"]


def test_units_end_by_the_run_deadline_never_a_fresh_duration(machine, layout):
    run(machine, layout, deadline=START + timedelta(seconds=1000, microseconds=5))
    assert "--property=RuntimeMaxSec=1000" in machine.started("bh-display")
    assert "--property=RuntimeMaxSec=1000" in machine.started("bh-game")


def test_nothing_starts_once_the_run_deadline_has_passed(machine, layout):
    code, status, _ = run(machine, layout, deadline=START - timedelta(seconds=1))
    assert (code, status["stage"], status["reason"]) == (1, "display", "deadline passed")
    assert not [c for c in machine.commands if c[0] == "systemd-run"]


def test_the_display_must_answer_within_its_bound(machine, layout):
    machine.display_up = False
    code, status, _ = run(machine, layout)
    assert (code, status["stage"], status["reason"]) == (1, "display", "display did not answer")
    assert machine.clock.now - START >= timedelta(seconds=worker.DISPLAY_SECONDS)
    assert not [c for c in machine.commands if "--unit=bh-game" in c]


def test_an_early_display_exit_is_caught(machine, layout):
    machine.display_up = False
    original = machine.start

    def exits(argv):
        original(argv)
        machine.units["bh-display"] = False

    machine.start = exits
    code, status, _ = run(machine, layout)
    assert (status["stage"], status["reason"]) == ("display", "display exited")
    assert machine.clock.now == START


def test_readiness_needs_an_authenticated_menu_within_the_startup_cap(machine, layout):
    machine.reply = lambda body: {"jsonrpc": "2.0", "id": body["id"],
                                  "result": {"state": "SPLASH"}}
    code, status, runner = run(machine, layout)
    assert (code, status["stage"], status["reason"]) == (1, "ready", "not ready: state SPLASH")
    assert machine.clock.now - START >= timedelta(seconds=worker.STARTUP_SECONDS)
    ids = [body["id"] for _, body, _ in machine.posts]
    assert len(ids) == len(set(ids)) > 100
    for url, body, timeout in machine.posts:
        assert url == "http://127.0.0.1:12346/"
        assert (body["jsonrpc"], body["method"], timeout) == ("2.0", "bh_inspect", 10)
        assert body["params"] == {"_bh_token": runner.token}


@pytest.mark.parametrize("reply,problem", [
    (lambda body: {"jsonrpc": "2.0", "id": "another", "result": {"state": "MENU"}},
     "reply id mismatch"),
    (lambda body: {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32000},
                   "result": {"state": "MENU"}}, "error reply"),
    (lambda body: {"jsonrpc": "2.0", "id": body["id"]}, "no result"),
])
def test_a_reply_that_does_not_answer_the_request_is_never_ready(machine, layout, reply,
                                                                 problem):
    machine.reply = reply
    _, status, _ = run(machine, layout)
    assert (status["stage"], status["reason"]) == ("ready", f"not ready: {problem}")


def test_the_game_exiting_during_startup_fails_the_ready_stage(machine, layout):
    original = machine.start

    def exits(argv):
        original(argv)
        machine.units["bh-game"] = False

    machine.start = exits
    _, status, _ = run(machine, layout)
    assert (status["stage"], status["reason"]) == ("ready", "game exited")


def test_a_failure_reports_capped_log_tails_without_the_token(machine, layout):
    machine.reply = lambda body: {"jsonrpc": "2.0", "id": body["id"], "result": {"state": "x"}}
    _, status, runner = run(machine, layout)
    tail = status["evidence"]["game_stderr"]
    assert len(tail) <= worker.TAIL_CHARS and tail.endswith("token=[token]\n")
    assert runner.token not in json.dumps(status)
    assert set(status["evidence"]) == {"display_stderr", "game_stderr"}
    WorkerStatus.model_validate(status)


def test_an_existing_account_is_reused(machine, layout):
    machine.user_exists = True
    code, _, _ = run(machine, layout)
    assert code == 0 and not [c for c in machine.commands if c[0] == "useradd"]
    assert stat.S_IMODE(layout.evidence.stat().st_mode) == 0o700


def test_an_unexpected_error_is_reported_as_a_failed_stage(machine, layout):
    machine.broken = "useradd"
    code, status, _ = run(machine, layout)
    assert (code, status["stage"], status["reason"]) == (1, "user", "unexpected RuntimeError")


@pytest.mark.parametrize("argv", [
    [],
    ["--bundle-name", "../escape", "--bundle-bytes", "1", "--bundle-sha256", "0" * 64,
     "--deadline", "2026-11-24T14:00:00+00:00"],
    ["--bundle-name", "bundle.tar", "--bundle-bytes", "1", "--bundle-sha256", "0" * 64,
     "--deadline", "2026-11-24T14:00:00"],
    ["--help"],
])
def test_bad_arguments_are_reported_in_the_status_file_not_by_a_bare_exit(machine, layout,
                                                                          argv):
    assert worker.main(argv, machine=machine, layout=layout) == 1
    status = json.loads(layout.status.read_text())
    assert (status["state"], status["stage"], status["reason"]) == (
        "failed", "packages", "bad arguments")
    assert machine.commands == [["dpkg-query", "-W", "-f", "${binary:Package}=${Version}\\n"]]


@pytest.mark.parametrize("name", ["worker.py", "driver.py"])
def test_the_worker_and_its_driver_import_only_the_standard_library(name):
    path = Path(worker.__file__).with_name(name)
    assert path.is_file()
    tree = ast.parse(path.read_text())
    assert not [node for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level], "no relative import"
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom)}
    assert imported and imported <= sys.stdlib_module_names


def test_a_missing_driver_fails_at_packages_before_any_install(machine, layout, tmp_path,
                                                               monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(worker, "__file__", str(elsewhere / "worker.py"))
    code, status, _ = run(machine, layout)
    assert (code, status["state"], status["stage"], status["reason"]) == (
        1, "failed", "packages", "driver unreadable")
    assert [c[0] for c in machine.commands] == ["dpkg-query"]


def test_the_driver_is_the_file_beside_the_worker(machine, layout, tmp_path, monkeypatch):
    beside = tmp_path / "beside"
    beside.mkdir()
    driver = Path(worker.__file__).with_name("driver.py")
    assert driver.is_file()
    (beside / "driver.py").write_text(driver.read_text() + '\nDECK = "BLUE"\n')
    monkeypatch.setattr(worker, "__file__", str(beside / "worker.py"))
    code, status, _ = run(machine, layout)
    decks = [body["params"]["deck"] for _, body, _ in machine.posts if body["method"] == "start"]
    assert (code, decks) == (0, ["BLUE"])
    assert json.loads((layout.worker / "results.json").read_text())["deck"] == "BLUE"


def test_the_shipped_pins_are_the_public_pilot_downloads():
    pins = tomllib.loads((Path(worker.__file__).parent / "pins.toml").read_text())
    assert [(p["name"], p["bytes"], p["sha256"], p["use"]) for p in pins["downloads"]] == [
        ("GE-Proton11-14-x86_64.tar.gz", 598620532,
         "26bcea3324b68dee68bf6b9b005f4425cdb9c29872e126d790df5abfebe332a8", "runner"),
        ("python3-umu-launcher_1.4.4-1_amd64_ubuntu-noble.deb", 661336,
         "86b7a234f77fbcd13699654656192a12ed3852ec2bcc721506ae4f91436b3793", "package"),
        ("umu-launcher_1.4.4-1_all_ubuntu-noble.deb", 12954,
         "8d11aa5bf0edaa988a4cbb04a1414a3d2249128913e6130e6a2a8528c9ef31b1", "package"),
    ]
    assert all(p["url"].startswith("https://github.com/") and p["url"].endswith("/" + p["name"])
               for p in pins["downloads"])
    assert len(pins["apt_packages"]) == 23
    assert {"xvfb", "mesa-utils", "bubblewrap", "python3"} <= set(pins["apt_packages"])


def test_the_controller_starts_the_worker_where_the_worker_expects_to_be():
    layout = worker.Layout()
    assert (install.WORKER_DIR, install.WORKER_FILES) == (
        str(layout.worker), ("worker.py", "driver.py", "pins.toml"))
    assert str(layout.status) in install.STATUS_COMMAND
    deadline = START + timedelta(hours=2)
    bundle = Bundle(path=Path("/stand-in") / "bundle.tar", name="bundle.tar",
                    bytes=len(BUNDLE), sha256=hashlib.sha256(BUNDLE).hexdigest())
    command = shlex.split(install.start_command(bundle, deadline))
    script = command.index(f"{layout.worker}/worker.py")
    assert command[script - 2:script] == ["/usr/bin/python3", "-I"]
    parsed = worker.parse_job(command[script + 1:])
    assert (parsed.bundle_name, parsed.bundle_bytes, parsed.deadline) == (
        "bundle.tar", len(BUNDLE), deadline)


def test_an_environment_value_that_cannot_be_quoted_is_refused():
    assert worker.environment_file({"B": "two words", "A": "Z:\\srv"}) == (
        "A='Z:\\srv'\nB='two words'\n")
    with pytest.raises(worker.StageFailed, match="cannot be quoted"):
        worker.environment_file({"A": "it's"})


class Opener:
    """Stands in for urllib's opener: it raises `error` or answers `body`, and connects
    nowhere."""

    def __init__(self, error=None, body=b""):
        self.error = error
        self.body = body

    def open(self, request, timeout):
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.body)


@pytest.mark.parametrize(("opener", "named"), [
    (Opener(http.client.RemoteDisconnected("closed")), "RemoteDisconnected"),
    (Opener(http.client.IncompleteRead(b"")), "IncompleteRead"),
    (Opener(TimeoutError("timed out")), "TimeoutError"),
    (Opener(body=b"not json"), "JSONDecodeError"),
], ids=["disconnected", "incomplete", "timeout", "garbled"])
def test_the_machine_reports_a_reply_it_cannot_read_as_a_lost_answer(monkeypatch, opener,
                                                                     named):
    monkeypatch.setattr(worker.urllib.request, "build_opener", lambda *handlers: opener)
    with pytest.raises(Exception) as caught:
        worker.Machine().post("http://127.0.0.1:12346/", {"id": "x"}, 10)
    assert (caught.type, str(caught.value)) == (OSError, named)


def test_the_machine_keeps_an_http_error_status_and_no_other_text(monkeypatch):
    def lost_with(error):
        monkeypatch.setattr(worker.urllib.request, "build_opener",
                            lambda *handlers: Opener(error))
        with pytest.raises(OSError) as caught:
            worker.Machine().post("http://127.0.0.1:12346/", {"id": "x"}, 10)
        return str(caught.value)

    server_error = urllib.error.HTTPError("http://127.0.0.1:12346/", 500,
                                          "Internal Server Error", {}, None)
    assert lost_with(server_error) == "HTTPError 500"
    assert lost_with(TimeoutError("timed out")) == "TimeoutError"


def test_the_machine_names_a_command_that_times_out_or_cannot_start(tmp_path):
    machine = worker.Machine()
    assert machine.run(["true"]).returncode == 0
    with pytest.raises(worker.StageFailed, match="false exit 1"):
        machine.run(["false"])
    assert machine.run(["false"], check=False).returncode == 1
    with pytest.raises(worker.StageFailed, match="sleep timed out"):
        machine.run(["sleep", "5"], timeout=0.1)
    with pytest.raises(worker.StageFailed, match="could not start"):
        machine.run([str(tmp_path / "missing")])
    assert machine.display_answers(tmp_path / "no-socket") is False
