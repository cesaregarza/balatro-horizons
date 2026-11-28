"""The worker's unit check as systemctl answers it, on the fake machine.

`systemctl is-active` exits 0 for a unit that runs, 3 for one that is not active and 4 for
one it does not know. Any other exit is a check that failed, which never reads as the game
or the display having exited. These tests keep the worker's own `Machine.unit_active` and
`Machine.run`, and stand in for the process call alone.
"""

import subprocess

import pytest
from worker_support import Clock, FakeMachine, fault_at, layout_in, run

from balatro_horizons.cloud.worker import worker

# A check that fails, a unit that is not active, and a unit systemctl does not know.
IDS = ["check-fails", "not-active", "unknown"]
LOST = "bh_action discard; answer TimeoutError after 0 s; status rejected"


class CheckedMachine(FakeMachine):
    """The fake machine with the worker's own unit check. Each `systemctl is-active` exits as
    `exit_of(unit)` says."""

    unit_active = worker.Machine.unit_active

    def __init__(self, layout, clock):
        super().__init__(layout, clock)
        self.exit_of = lambda unit: 0

    def run(self, argv, **options):
        if argv[:2] == ["systemctl", "is-active"]:
            return worker.Machine.run(self, argv, **options)
        return super().run(argv, **options)


@pytest.fixture
def layout(tmp_path):
    return layout_in(tmp_path)


@pytest.fixture
def machine(layout, monkeypatch):
    machine = CheckedMachine(layout, Clock())

    def process(argv, **options):
        # Only the unit check reaches the process call. Every other command is the fake's.
        assert argv[:3] == ["systemctl", "is-active", "--quiet"]
        return subprocess.CompletedProcess(argv, machine.exit_of(argv[3]), b"", b"")

    monkeypatch.setattr(subprocess, "run", process)
    return machine


@pytest.mark.parametrize(("code", "reason", "running"), [
    (1, f"uncertain move: {LOST}", "unavailable"),
    (3, f"game exited during {LOST}", "no"),
    (4, f"game exited during {LOST}", "no"),
], ids=IDS)
def test_a_lost_answer_reads_as_a_game_exit_only_when_systemctl_reports_one(machine, layout,
                                                                            code, reason,
                                                                            running):
    # The game rejects the discard and its answer is lost. From the first status ask on, the
    # game's check exits `code`.
    def exit_of(unit):
        asked = machine.local_api is not None and machine.local_api.bodies_for(
            "bh_request_status")
        return code if unit == "bh-game" and asked else 0

    machine.exit_of = exit_of
    fault_at(machine, "reject")
    _, status, _ = run(machine, layout)
    assert (status["stage"], status["reason"]) == ("play", reason)
    assert status["observed"]["game_unit"] == (
        f"running={running} main=unavailable result=unavailable journal_oom=no")


@pytest.mark.parametrize(("code", "reason"), [
    (1, "systemctl exit 1"), (3, "display exited"), (4, "display exited"),
], ids=IDS)
def test_the_display_reads_as_exited_only_when_systemctl_reports_it(machine, layout, code,
                                                                    reason):
    machine.display_up = False
    machine.exit_of = lambda unit: code
    _, status, _ = run(machine, layout)
    assert (status["stage"], status["reason"]) == ("display", reason)


@pytest.mark.parametrize(("code", "reason"), [
    (1, "systemctl exit 1"), (3, "game exited"), (4, "game exited"),
], ids=IDS)
def test_the_game_reads_as_exited_before_it_is_ready_only_when_systemctl_reports_it(
        machine, layout, code, reason):
    machine.exit_of = lambda unit: code if unit == "bh-game" else 0
    _, status, _ = run(machine, layout)
    assert (status["stage"], status["reason"]) == ("ready", reason)
