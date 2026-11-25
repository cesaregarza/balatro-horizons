"""Run lifecycle and teardown triggers against the fake provider."""

import json
from datetime import UTC, timedelta

import pytest
from cloud_support import Cloud, cloud_settings

from balatro_horizons.cloud.fake import SimulatedCrash
from balatro_horizons.cloud.settings import run_tag


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def test_run_and_absolute_deadline_are_durable_before_the_create(cloud):
    cloud.provider.crash_at.add("before_create")
    with pytest.raises(SimulatedCrash):
        cloud.controller().launch("r1")
    written = json.loads((cloud.root / "runs/r1.json").read_text())
    assert written["state"] == "provisioning"
    record = cloud.ledger.get("r1")
    assert record.deadline.tzinfo == UTC
    assert record.deadline - record.created_at == timedelta(hours=2)


def test_droplet_is_tagged_with_owner_and_run_tags_at_creation(cloud):
    cloud.controller().launch("r1")
    creates = [call for call in cloud.provider.calls if call[0] == "create"]
    assert creates == [("create", "bh-r1", ("bh-owner:test-ctl", run_tag("r1")))]


def test_success_tears_down_and_settles_elapsed_time(cloud):
    controller = cloud.launched()
    cloud.clock.advance(1800)
    cloud.channel.finish("r1", cloud.clock())
    controller.tick()
    record = cloud.assert_torn_down("r1", "succeeded")
    assert record.settled_usd == pytest.approx(0.05)


def test_boot_failure_tears_down_after_the_boot_timeout(cloud):
    cloud.provider.boot = False
    controller = cloud.controller()
    controller.launch("r1")
    controller.tick()
    assert cloud.ledger.get("r1").state == "provisioning"
    cloud.clock.advance(301)
    controller.tick()
    cloud.assert_torn_down("r1", "boot_failed")


def test_droplet_vanishing_during_boot_is_a_boot_failure_that_cannot_be_verified(cloud):
    cloud.provider.boot = False
    controller = cloud.controller()
    controller.launch("r1")
    cloud.provider.inventory.pop(cloud.owned_ids("r1")[0])
    controller.tick()
    record = cloud.ledger.get("r1")
    # No DELETE of ours was acknowledged, so the 404 proves nothing.
    assert (record.state, record.teardown_reason) == ("cleanup_unverified", "boot_failed")
    assert cloud.provider.deleted() == []


def test_create_failure_leaves_a_verified_run_and_nothing_created(cloud):
    cloud.provider.fail_create = True
    assert cloud.controller().launch("r1")["outcome"] == "create_failed"
    cloud.assert_torn_down("r1", "create_failed")


def test_partial_create_deletes_the_droplet_the_failed_call_left(cloud):
    cloud.provider.partial_create = True
    assert cloud.controller().launch("r1")["outcome"] == "create_failed"
    left = [call[1] for call in cloud.provider.calls if call[0] == "delete"]
    assert len(left) == 1
    cloud.assert_torn_down("r1", "create_failed")


def test_deadline_tears_down_even_with_a_live_heartbeat(cloud):
    controller = cloud.launched()
    cloud.clock.advance(2 * 3600)
    cloud.channel.beat("r1", cloud.clock())
    controller.tick()
    cloud.assert_torn_down("r1", "deadline")


def test_cancel_request_is_honored_on_the_next_tick(cloud):
    controller = cloud.launched()
    cloud.channel.beat("r1", cloud.clock())
    assert controller.cancel("r1")["state"] == "running"
    controller.tick()
    cloud.assert_torn_down("r1", "cancelled")


def test_cancel_now_tears_down_in_the_same_call(cloud):
    controller = cloud.launched()
    assert controller.cancel("r1", now=True)["state"] == "torn_down"
    cloud.assert_torn_down("r1", "cancelled")


def test_lost_heartbeat_tears_down_after_the_grace_window(cloud):
    controller = cloud.launched()
    cloud.clock.advance(100)
    cloud.channel.beat("r1", cloud.clock())
    cloud.clock.advance(100)
    controller.tick()
    assert cloud.ledger.get("r1").state == "running"
    cloud.clock.advance(21)
    controller.tick()
    cloud.assert_torn_down("r1", "heartbeat_lost")


def test_controller_error_tears_live_runs_down_before_raising(cloud):
    cloud.launched()

    def broken(run_id):
        raise RuntimeError("channel exploded")

    cloud.channel.report = broken
    with pytest.raises(RuntimeError):
        cloud.controller().supervise()
    cloud.assert_torn_down("r1", "controller_error")


def test_launch_is_refused_when_worst_case_exceeds_the_remaining_budget(tmp_path):
    cloud = Cloud(tmp_path, settings=cloud_settings(infrastructure_budget_usd=0.19))
    result = cloud.controller().launch("r1")
    assert result == {"outcome": "refused", "run_id": "r1", "reasons": ["INFRASTRUCTURE_BUDGET"]}
    assert cloud.ledger.get("r1") is None
    assert not [call for call in cloud.provider.calls if call[0] == "create"]


def test_settled_spend_reduces_the_budget_for_later_launches(tmp_path):
    cloud = Cloud(tmp_path, settings=cloud_settings(infrastructure_budget_usd=0.3))
    controller = cloud.launched()
    cloud.clock.advance(3600 + 1800)
    cloud.channel.finish("r1", cloud.clock())
    controller.tick()
    assert cloud.ledger.get("r1").settled_usd == pytest.approx(0.15)
    assert controller.launch("r2")["reasons"] == ["INFRASTRUCTURE_BUDGET"]


def test_second_concurrent_launch_is_refused(cloud):
    controller = cloud.launched()
    assert controller.launch("r2")["reasons"] == ["CONCURRENCY_LIMIT"]


def test_repeated_launch_for_a_run_id_creates_nothing(cloud):
    controller = cloud.controller()
    assert controller.launch("r1")["outcome"] == "launched"
    assert controller.launch("r1")["outcome"] == "duplicate"
    controller.cancel("r1", now=True)
    assert controller.launch("r1")["outcome"] == "duplicate"
    assert len([call for call in cloud.provider.calls if call[0] == "create"]) == 1


def test_repeated_teardown_after_verification_is_a_no_op(cloud):
    controller = cloud.launched()
    controller.cancel("r1", now=True)
    calls = list(cloud.provider.calls)
    record = cloud.ledger.get("r1")
    assert controller.teardown.run(record, "cancelled") is record
    controller.cancel("r1", now=True)
    controller.tick()
    assert cloud.provider.calls == calls


def test_invalid_run_ids_are_refused_before_any_write(cloud):
    with pytest.raises(ValueError, match="INVALID_RUN_ID"):
        cloud.controller().launch("../escape")
    assert not cloud.root.exists()
