"""A crash and controller restart at every lifecycle step leaves nothing behind."""

import os

import pytest
from cloud_support import Cloud

from balatro_horizons.cloud.fake import SimulatedCrash

# The successful lifecycle writes the ledger ten times: the new run, its Droplet ID, its
# address, the worker's start, running, teardown started, the DELETE attempt, the
# acknowledged DELETE, the action-log check, and verified.
LEDGER_WRITES = 10
# A crash on this write loses the acknowledgment of a DELETE the provider carried out.
ACKNOWLEDGMENT_WRITE = 8


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def crash_on_write(cloud, monkeypatch, number):
    save = cloud.ledger.save
    count = {"writes": 0}

    def crashing(record):
        count["writes"] += 1
        if count["writes"] == number:
            raise SimulatedCrash(f"write {number}")
        save(record)

    monkeypatch.setattr(cloud.ledger, "save", crashing)
    return lambda: monkeypatch.setattr(cloud.ledger, "save", save)


def full_lifecycle(cloud):
    """Launch, install, run once play starts, then finish, copy the results and tear down."""
    controller = cloud.controller()
    controller.launch("r1")
    controller.tick()
    controller.tick()
    cloud.channel.finish("r1")
    controller.tick()


def restart_and_settle(cloud):
    restarted = cloud.controller()
    restarted.recover()
    for _ in range(100):
        restarted.tick()
        if not any(record.live for record in cloud.ledger.runs()):
            return
        cloud.clock.advance(cloud.settings.poll_seconds)
    raise AssertionError("a live run outlived 100 controller ticks")


@pytest.mark.parametrize("number", [n for n in range(1, LEDGER_WRITES + 1)
                                    if n != ACKNOWLEDGMENT_WRITE])
def test_crash_at_each_ledger_write_then_restart_leaves_nothing(cloud, monkeypatch, number):
    restore = crash_on_write(cloud, monkeypatch, number)
    with pytest.raises(SimulatedCrash):
        full_lifecycle(cloud)
    restore()
    restart_and_settle(cloud)
    record = cloud.ledger.get("r1")
    if number == 1:
        assert record is None and cloud.owned_ids("r1") == []
        assert not [call for call in cloud.provider.calls if call[0] == "create"]
    else:
        assert record.state == "torn_down"
        assert cloud.owned_ids("r1") == []
    cloud.assert_unrelated_survive()


@pytest.mark.parametrize("point", ["after_create", "before_delete"])
def test_crash_inside_a_provider_call_then_restart_leaves_nothing(cloud, point):
    cloud.provider.crash_at.add(point)
    with pytest.raises(SimulatedCrash):
        full_lifecycle(cloud)
    restart_and_settle(cloud)
    assert cloud.ledger.get("r1").state == "torn_down"
    assert cloud.owned_ids("r1") == []
    cloud.assert_unrelated_survive()


def lose_the_acknowledgment(cloud, monkeypatch):
    restore = crash_on_write(cloud, monkeypatch, ACKNOWLEDGMENT_WRITE)
    with pytest.raises(SimulatedCrash):
        full_lifecycle(cloud)
    restore()
    restarted = cloud.controller()
    restarted.recover()
    for _ in range(10):
        restarted.tick()
        cloud.reconcile()
    return restarted


def test_crash_before_the_acknowledgment_is_written_clears_on_the_action_log(cloud,
                                                                              monkeypatch):
    restarted = lose_the_acknowledgment(cloud, monkeypatch)
    record = cloud.assert_torn_down("r1", "succeeded")
    assert record.resources[0].acknowledged_at is None
    assert len(cloud.provider.deleted()) == 1
    assert restarted.launch("r2")["outcome"] == "launched"


def test_crash_before_the_acknowledgment_is_written_holds_without_a_logged_destroy(
    cloud, monkeypatch,
):
    cloud.provider.log_destroys = False
    restarted = lose_the_acknowledgment(cloud, monkeypatch)
    record = cloud.ledger.get("r1")
    assert (record.state, record.settled_usd) == ("cleanup_unverified", None)
    assert len(cloud.provider.deleted()) == 1
    assert restarted.launch("r2")["outcome"] == "refused"
    cloud.assert_unrelated_survive()


def test_crash_before_the_create_request_holds_the_run_rather_than_guess(cloud):
    # The ledger cannot tell a crash before the request from one during it.
    cloud.provider.crash_at.add("before_create")
    with pytest.raises(SimulatedCrash):
        full_lifecycle(cloud)
    restarted = cloud.controller()
    restarted.recover()
    for _ in range(100):
        restarted.tick()
        cloud.reconcile()
        cloud.clock.advance(cloud.settings.poll_seconds)
    assert cloud.ledger.get("r1").state == "create_unresolved"
    assert "CREATE_UNRESOLVED" in restarted.launch("r2")["reasons"]
    assert len([call for call in cloud.provider.calls if call[0] == "create"]) == 1
    cloud.assert_unrelated_survive()


def test_run_still_provisioning_at_restart_is_torn_down_not_adopted(cloud):
    cloud.provider.boot = False
    cloud.controller().launch("r1")
    cloud.controller().recover()
    cloud.assert_torn_down("r1", "restart_while_provisioning")


def test_running_run_keeps_going_if_a_heartbeat_arrives_within_grace(cloud):
    cloud.launched()
    cloud.clock.advance(600)
    restarted = cloud.controller()
    restarted.recover()
    assert cloud.ledger.get("r1").state == "running"
    cloud.clock.advance(60)
    cloud.channel.progress("r1")
    cloud.clock.advance(100)
    restarted.tick()
    assert cloud.ledger.get("r1").state == "running"


def test_running_run_without_a_heartbeat_after_restart_is_torn_down(cloud):
    cloud.launched()
    restarted = cloud.controller()
    restarted.recover()
    cloud.clock.advance(121)
    restarted.tick()
    cloud.assert_torn_down("r1", "heartbeat_lost")


def test_running_run_past_its_deadline_at_restart_is_torn_down(cloud):
    cloud.launched()
    cloud.clock.advance(2 * 3600)
    cloud.controller().recover()
    cloud.assert_torn_down("r1", "deadline")


def test_interrupted_ledger_replace_keeps_the_previous_record_readable(cloud, monkeypatch):
    cloud.launched()
    before = cloud.ledger.get("r1")

    def interrupted(source, target):
        raise SimulatedCrash("power loss")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(SimulatedCrash):
        cloud.controller().cancel("r1")
    monkeypatch.undo()
    assert list((cloud.root / "runs").glob("*.tmp"))
    assert cloud.ledger.runs() == [before]
