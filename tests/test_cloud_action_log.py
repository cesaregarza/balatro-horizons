"""Teardown's action-log path: a completed destroy for the ID, started no earlier than the
run's creation less the clock-skew allowance, proves the deletion. A 404 and a listing
that omits the ID must still follow, and nothing else in the log counts."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from cloud_support import Cloud, DigitalOceanStub

from balatro_horizons.cloud.digitalocean import DigitalOceanClient
from balatro_horizons.cloud.ledger import Ledger

SKEW = timedelta(minutes=5)


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def deleted_outside(cloud, status="completed", started=None, logged_id=None):
    """Delete the run's Droplet behind the controller's back; the log shows a destroy."""
    droplet_id = cloud.owned_ids("r1")[0]
    cloud.provider.inventory.pop(droplet_id)
    log = getattr(cloud.provider, "log_destroy", None)
    assert log is not None, "the fake provider keeps an action log of destroys"
    log(logged_id or droplet_id, status=status, at=started)
    return droplet_id


def assert_held(cloud):
    record = Ledger(cloud.root).get("r1")
    assert (record.state, record.verified_at, record.settled_usd) == (
        "cleanup_unverified", None, None)
    assert "CLEANUP_UNVERIFIED" in cloud.alert_codes()
    return record


def test_a_droplet_deleted_outside_the_controller_clears_on_its_destroy(cloud):
    controller = cloud.launched()
    deleted_outside(cloud)
    controller.cancel("r1", now=True)
    record = cloud.assert_torn_down("r1", "cancelled")
    assert cloud.provider.deleted() == []
    resource = record.resources[0]
    assert resource.acknowledged_at is None
    assert (resource.destroy_evidence.status, resource.destroy_evidence.resource_id) == (
        "completed", resource.id)


def test_a_destroy_at_the_edge_of_the_clock_skew_allowance_still_counts(cloud):
    controller = cloud.launched()
    deleted_outside(cloud, started=cloud.ledger.get("r1").created_at - SKEW)
    controller.cancel("r1", now=True)
    cloud.assert_torn_down("r1", "cancelled")


@pytest.mark.parametrize("case", ["no destroy", "another ID", "errored", "in progress",
                                  "before the run"])
def test_nothing_else_in_the_log_clears_a_droplet_that_reads_404(cloud, case):
    controller = cloud.launched()
    created = cloud.ledger.get("r1").created_at
    if case == "no destroy":
        cloud.provider.inventory.pop(cloud.owned_ids("r1")[0])
    else:
        deleted_outside(cloud, **{
            "another ID": {"logged_id": cloud.unrelated[0].id},
            "errored": {"status": "errored"},
            "in progress": {"status": "in-progress"},
            "before the run": {"started": created - SKEW - timedelta(seconds=1)},
        }[case])
    controller.cancel("r1", now=True)
    cloud.reconcile()
    controller.tick()
    record = assert_held(cloud)
    assert cloud.provider.deleted() == []
    # The log was read, and what it showed is on the record.
    assert getattr(record.resources[0], "destroy_checked_at", None) is not None


def test_evidence_that_names_another_id_never_counts(cloud):
    controller = cloud.launched()
    cloud.provider.inventory.pop(cloud.owned_ids("r1")[0])
    controller.cancel("r1", now=True)
    record = assert_held(cloud)
    resource = record.resources[0]
    assert "destroy_evidence" in type(resource).model_fields
    evidence = {"action_id": 1, "status": "completed", "started_at": record.created_at}

    def with_evidence(resource_id):
        changed = resource.model_dump() | {
            "destroy_evidence": evidence | {"resource_id": resource_id}}
        return record.model_copy(update={"resources": [resource.model_validate(changed)]})

    teardown = cloud.controller().teardown
    assert teardown.verified_absent(with_evidence(resource.id + 1)) is False
    assert teardown.verified_absent(with_evidence(resource.id)) is True


@pytest.mark.parametrize("later", ["errored", "in-progress"])
def test_completed_evidence_outlasts_a_later_lookup_that_shows_less(cloud, later):
    controller = cloud.launched()
    droplet_id = deleted_outside(cloud)
    record = cloud.ledger.get("r1")
    controller.teardown.corroborate(record)
    assert record.resources[0].destroy_evidence.status == "completed"
    # The log no longer lists the completed destroy, only a lesser action for the same ID.
    cloud.provider.destroys.clear()
    cloud.provider.log_destroy(droplet_id, status=later)
    controller.teardown.corroborate(record)
    assert record.resources[0].destroy_evidence.status == "completed"
    controller.cancel("r1", now=True)
    cloud.assert_torn_down("r1", "cancelled")


def test_an_id_the_controller_may_not_delete_never_clears_on_a_destroy(cloud):
    controller = cloud.launched()
    droplet_id = cloud.owned_ids("r1")[0]
    cloud.provider.inventory[droplet_id] = replace(cloud.provider.inventory[droplet_id], tags=())
    controller.cancel("r1", now=True)
    cloud.provider.inventory.pop(droplet_id)
    log = getattr(cloud.provider, "log_destroy", None)
    assert log is not None
    log(droplet_id)
    cloud.reconcile()
    controller.tick()
    record = assert_held(cloud)
    assert droplet_id in record.released


def test_every_teardown_records_what_the_action_log_showed(cloud):
    controller = cloud.launched()
    cloud.channel.finish("r1", cloud.clock())
    controller.tick()
    resource = cloud.assert_torn_down("r1", "succeeded").resources[0]
    assert getattr(resource, "destroy_checked_at", None) is not None
    assert resource.delete_requested_at <= resource.acknowledged_at
    assert resource.destroy_evidence.status == "completed"


def test_a_failed_action_log_read_never_delays_settling(cloud):
    controller = cloud.launched()
    cloud.provider.action_failures = 10
    cloud.channel.finish("r1", cloud.clock())
    controller.tick()
    resource = cloud.assert_torn_down("r1", "succeeded").resources[0]
    assert ("destroys",) in cloud.provider.calls
    assert (resource.destroy_evidence, resource.destroy_checked_at) == (None, None)


def test_each_delete_attempt_is_recorded_before_it_is_sent(cloud):
    controller = cloud.launched()
    cloud.provider.delete_failures = 1
    controller.cancel("r1", now=True)
    resource = cloud.assert_torn_down("r1", "cancelled").resources[0]
    deletes = [call for call in cloud.provider.calls if call[0] == "delete"]
    assert len(deletes) == 2
    # The fake provider refuses any DELETE the ledger on disk does not already show.
    assert getattr(resource, "delete_requested_at", None) == resource.acknowledged_at


def test_a_delete_answered_with_a_server_error_clears_on_the_logged_destroy(tmp_path):
    stub = DigitalOceanStub()
    cloud = Cloud(tmp_path / "cloud")
    stub.clock = cloud.clock
    cloud.provider = DigitalOceanClient("test-token", transport=httpx.MockTransport(stub),
                                        sleep=cloud.clock.advance)
    controller = cloud.launched()
    # The provider carried out the DELETE, but the answer was lost to a 500.
    stub.delete_status = 500
    controller.cancel("r1", now=True)
    record = Ledger(cloud.root).get("r1")
    assert (record.state, record.teardown_reason) == ("torn_down", "cancelled")
    assert stub.droplets == {}
    assert record.resources[0].acknowledged_at is None
