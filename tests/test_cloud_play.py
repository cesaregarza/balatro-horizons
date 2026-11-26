"""A running run, read over the SSH channel: progress, the heartbeat, failures and the
results copy. The fake transport answers every call, so nothing connects.

Each test starts from a run its boot has just moved to running; the boot's own rule is
tested with the boot. The heartbeat grace is 120 seconds and the run deadline falls two
hours after the create.
"""

import json
import stat
from datetime import timedelta

import pytest
from cloud_support import RESULTS, Cloud, worker_status

from balatro_horizons.cloud.ledger import Ledger
from balatro_horizons.cloud.status import status_report

RESULTS_BYTES = 1 << 20


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def running(cloud):
    """Launch, install, and move the run to running as its boot would."""
    controller = cloud.controller(channel=None)
    assert controller.launch("r1")["outcome"] == "launched"
    controller.tick()
    record = cloud.ledger.get("r1")
    assert record.state == "provisioning" and record.worker_started_at is not None
    record.grace_from = cloud.clock()
    record.move("running", record.grace_from)
    cloud.ledger.save(record)
    cloud.transport.calls.clear()
    return controller


def after_create(cloud, at):
    """Seconds from the create, when the run also moved to running, to `at`."""
    return (at - cloud.ledger.get("r1").created_at).total_seconds()


def copied(cloud):
    return cloud.root / "results" / "r1.json"


def test_progress_keeps_a_run_alive_past_the_grace(cloud):
    controller = running(cloud)
    cloud.transport.statuses = [worker_status(actions=count) for count in (1, 2, 3, 4)]
    for _ in range(4):
        cloud.clock.advance(100)
        controller.tick()
    record = cloud.ledger.get("r1")
    assert record.state == "running"
    assert (getattr(record, "actions_seen", None), after_create(cloud, record.grace_from)) == (
        4, 400)
    assert record.worker_status.actions == 4


def test_the_grace_restarts_from_when_the_read_returns(cloud):
    # The first read starts 100 seconds in and takes 30 to bring a higher count.
    controller = running(cloud)
    cloud.transport.statuses = [worker_status(actions=1)]
    cloud.transport.durations = {"status": 30}
    cloud.clock.advance(100)
    controller.tick()
    assert after_create(cloud, cloud.ledger.get("r1").grace_from) == 130
    cloud.transport.durations = {}
    cloud.clock.advance(115)
    controller.tick()
    assert cloud.ledger.get("r1").state == "running"
    cloud.clock.advance(6)
    controller.tick()
    cloud.assert_torn_down("r1", "heartbeat_lost")


def test_a_stall_loses_the_heartbeat_though_every_read_succeeds(cloud):
    controller = running(cloud)
    cloud.transport.statuses = [worker_status(actions=3)]
    graces = []
    for seconds in (60, 60, 61):
        cloud.clock.advance(seconds)
        controller.tick()
        graces.append(after_create(cloud, cloud.ledger.get("r1").grace_from))
    # Only the first read shows a higher count; the same count again is no progress.
    assert graces == [60, 60, 60]
    record = cloud.assert_torn_down("r1", "heartbeat_lost")
    assert cloud.transport.steps() == ["status", "status", "status"]
    assert getattr(record, "results_copy", None) is None and not copied(cloud).exists()


def test_a_failed_status_during_play_tears_down_in_that_tick_and_keeps_its_evidence(cloud):
    controller = running(cloud)
    failed = worker_status("failed", "play", "move refused: select", actions=2)
    failed["evidence"] = {"game_stderr": "a capped tail"}
    cloud.transport.statuses = [failed]
    cloud.clock.advance(10)
    controller.tick()
    record = cloud.assert_torn_down("r1", "worker_failed:play")
    assert (record.worker_status.reason, record.worker_status.evidence) == (
        "move refused: select", {"game_stderr": "a capped tail"})
    assert cloud.transport.steps() == ["status"] and not copied(cloud).exists()


@pytest.mark.parametrize("status", [b'{"stage": "play", "state": "lost"}', b"[1, 2]",
                                    b"x" * (64 * 1024 + 1)], ids=["state", "array", "large"])
def test_a_malformed_status_during_play_tears_down_in_that_tick(cloud, status):
    controller = running(cloud)
    cloud.transport.statuses = [status]
    cloud.clock.advance(10)
    controller.tick()
    cloud.assert_torn_down("r1", "worker_status_malformed")


def test_a_finished_game_is_copied_then_torn_down_in_the_same_tick(cloud):
    controller = running(cloud)
    cloud.transport.statuses = [worker_status("finished", "play", actions=5)]
    seen_at_delete = []
    delete = cloud.provider.delete_droplet

    def delete_droplet(droplet_id):
        kept = json.loads((cloud.root / "runs" / "r1.json").read_text())
        seen_at_delete.append((copied(cloud).exists(), kept.get("results_copy")))
        return delete(droplet_id)

    cloud.provider.delete_droplet = delete_droplet
    cloud.clock.advance(10)
    controller.tick()
    record = cloud.assert_torn_down("r1", "succeeded")
    assert getattr(record, "results_copy", None) == "copied"
    assert json.loads(copied(cloud).read_text()) == RESULTS
    assert stat.S_IMODE(copied(cloud).stat().st_mode) == 0o600
    assert seen_at_delete[:1] == [(True, "copied")]
    assert cloud.transport.steps() == ["status", "results"]
    assert (record.worker_status.state, record.worker_status.outcome) == ("finished", "game_over")


def test_the_copy_reads_at_most_one_byte_past_its_cap_with_the_time_left(cloud):
    controller = running(cloud)
    cloud.transport.statuses = [worker_status("finished", "play", actions=5)]
    cloud.clock.advance(7200 - 40)
    controller.tick()
    _, argv, _, timeout = cloud.transport.calls[-1]
    assert argv[-1].startswith(f"head -c {RESULTS_BYTES + 1} ") and "results.json" in argv[-1]
    assert timeout == 40
    cloud.assert_torn_down("r1", "succeeded")


@pytest.mark.parametrize(("failure", "results", "named"), [
    ("failed", None, "ssh_failed"),
    ("unreachable", None, "ssh_unreachable"),
    ("timed_out", None, "ssh_timed_out"),
    (None, b"", "results_missing"),
    (None, b"x" * (RESULTS_BYTES + 1), "results_too_large"),
    (None, b"not json", "results_malformed"),
    (None, b"[1, 2]", "results_malformed"),
], ids=["failed", "unreachable", "timed_out", "empty", "large", "text", "array"])
def test_each_failed_copy_is_named_and_the_teardown_follows_in_that_tick(cloud, failure,
                                                                         results, named):
    controller = running(cloud)
    cloud.transport.statuses = [worker_status("finished", "play", actions=5)]
    if failure:
        cloud.transport.failures = {"results": failure}
    if results is not None:
        cloud.transport.results = results
    cloud.clock.advance(10)
    controller.tick()
    record = cloud.assert_torn_down("r1", "succeeded")
    assert getattr(record, "results_copy", None) == named
    assert not copied(cloud).exists()


def test_a_copy_cut_off_by_the_run_deadline_is_named_for_it(cloud):
    # The read returns five seconds before the deadline, so the copy may take five seconds,
    # and it times out at that cap.
    controller = running(cloud)
    cloud.transport.statuses = [worker_status("finished", "play", actions=5)]
    cloud.transport.durations = {"status": 5}
    cloud.transport.failures = {"results": "timed_out"}
    cloud.clock.advance(7200 - 10)
    controller.tick()
    record = cloud.assert_torn_down("r1", "succeeded")
    assert getattr(record, "results_copy", None) == "no_time_left"
    assert cloud.transport.calls[-1][3] == 5


def supervised(controller):
    """Supervise until no run needs it; the name of any error that escaped, or None."""
    try:
        controller.supervise()
    except Exception as error:
        return type(error).__name__
    return None


def test_a_copy_the_controller_cannot_write_is_named_and_the_run_still_succeeds(cloud):
    # A file where the results directory belongs stops the write, as a full disk would.
    controller = running(cloud)
    cloud.transport.statuses = [worker_status("finished", "play", actions=5)]
    (cloud.root / "results").write_text("")
    cloud.clock.advance(10)
    escaped = supervised(controller)
    record = cloud.ledger.get("r1")
    assert (escaped, record.teardown_reason, getattr(record, "results_copy", None)) == (
        None, "succeeded", "results_unwritable")
    cloud.assert_torn_down("r1", "succeeded")


def test_a_results_file_nested_too_deeply_to_parse_is_malformed(cloud):
    # Well within the size cap, and far deeper than the parser goes.
    controller = running(cloud)
    cloud.transport.statuses = [worker_status("finished", "play", actions=5)]
    cloud.transport.results = b"[" * (RESULTS_BYTES // 2)
    cloud.clock.advance(10)
    escaped = supervised(controller)
    record = cloud.ledger.get("r1")
    assert (escaped, record.teardown_reason, getattr(record, "results_copy", None)) == (
        None, "succeeded", "results_malformed")
    cloud.assert_torn_down("r1", "succeeded")
    assert not copied(cloud).exists()


def test_a_cancel_during_play_tears_down_without_a_read_or_a_copy(cloud):
    controller = running(cloud)
    cloud.transport.statuses = [worker_status("finished", "play", actions=5)]
    controller.cancel("r1")
    controller.tick()
    record = cloud.assert_torn_down("r1", "cancelled")
    assert cloud.transport.steps() == []
    assert getattr(record, "results_copy", None) is None and not copied(cloud).exists()


def test_the_running_read_may_take_only_the_time_left(cloud):
    controller = running(cloud)
    cloud.transport.statuses = [worker_status(actions=1)]
    cloud.clock.advance(7200 - 30)
    controller.tick()
    assert [call[3] for call in cloud.transport.calls] == [30]
    assert cloud.ledger.get("r1").state == "running"


def test_status_shows_how_the_copy_went(cloud):
    controller = running(cloud)
    cloud.transport.statuses = [worker_status("finished", "play", actions=5)]
    cloud.clock.advance(10)
    controller.tick()
    report = status_report(Ledger(cloud.root), cloud.settings, cloud.clock())
    assert [run.get("results_copy") for run in report["runs"]] == ["copied"]
    assert "TESTSEED" not in json.dumps(report)


def kept_from_main(created_at):
    """A run torn down by the code before play existed, whose kept status says ready."""
    at = created_at.isoformat()
    return {
        "run_id": "r0", "state": "torn_down", "created_at": at,
        "deadline": (created_at + timedelta(hours=2)).isoformat(), "reserved_usd": 0.2,
        "resources": [{"kind": "droplet", "id": 4001, "delete_requested_at": at,
                       "acknowledged_at": at, "destroy_evidence": None,
                       "destroy_checked_at": at}],
        "released": [], "create_attempted_at": at, "create_rejected": False,
        "address": "192.0.2.9", "last_ssh_failure": None, "worker_started_at": at,
        "worker_status": {"stage": "ready", "state": "ready", "reason": None,
                          "started_at": {"ready": at}, "finished_at": None,
                          "observed": {"kernel": "6.8.0-test"}, "evidence": {}},
        "grace_from": at, "cancel_requested": False, "teardown_reason": "succeeded",
        "teardown_started_at": at, "verified_at": at, "settled_usd": 0.0,
        "history": [{"at": at, "state": state, "reason": reason} for state, reason in (
            ("provisioning", None), ("running", None), ("tearing_down", "succeeded"),
            ("torn_down", "succeeded"))],
    }


def test_a_run_kept_with_a_ready_status_still_loads_and_shows(cloud):
    runs = cloud.root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "r0.json").write_text(json.dumps(kept_from_main(cloud.clock())))
    record = Ledger(cloud.root).get("r0")
    kept = record.worker_status
    assert (kept.state, kept.stage) == ("ready", "ready")
    assert (getattr(kept, "actions", 0), getattr(kept, "outcome", None)) == (0, None)
    report = status_report(Ledger(cloud.root), cloud.settings, cloud.clock())
    assert [(run["run_id"], run.get("results_copy")) for run in report["runs"]] == [("r0", None)]
