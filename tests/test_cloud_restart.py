"""A crash and controller restart at every lifecycle step leaves nothing behind."""

import os

import pytest
from cloud_support import Cloud

from balatro_horizons.cloud.fake import SimulatedCrash

# The successful lifecycle writes the ledger five times: the new run, its Droplet
# ID, running, teardown started, and verified.
LEDGER_WRITES = 5


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
    controller = cloud.controller()
    controller.launch("r1")
    controller.tick()
    cloud.channel.finish("r1", cloud.clock())
    controller.tick()


def restart_and_settle(cloud):
    restarted = cloud.controller()
    restarted.recover()
    while restarted.tick():
        cloud.clock.advance(cloud.settings.poll_seconds)


@pytest.mark.parametrize("number", range(1, LEDGER_WRITES + 1))
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


@pytest.mark.parametrize("point", ["before_create", "after_create", "before_delete", "after_delete"])
def test_crash_inside_a_provider_call_then_restart_leaves_nothing(cloud, point):
    cloud.provider.crash_at.add(point)
    with pytest.raises(SimulatedCrash):
        full_lifecycle(cloud)
    restart_and_settle(cloud)
    assert cloud.ledger.get("r1").state == "torn_down"
    assert cloud.owned_ids("r1") == []
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
    cloud.channel.beat("r1", cloud.clock())
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
