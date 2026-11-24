"""The reconciler works without the controller and never races it."""

import pytest
from cloud_support import Cloud

from balatro_horizons.cloud.ledger import LedgerBusy
from balatro_horizons.cloud.settings import run_tag


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def test_orphan_for_a_run_the_ledger_never_recorded_is_deleted_and_verified(cloud):
    orphan = cloud.provider.seed(cloud.settings.owner_tag, run_tag("lost"))
    result = cloud.reconcile()
    assert result["orphans"] == [{"run_id": "lost", "state": "torn_down", "reason": "orphan",
                                  "droplet_id": orphan.id}]
    assert orphan.id not in cloud.provider.inventory
    cloud.assert_torn_down("lost", "orphan")


def test_orphan_for_a_finished_run_reopens_it_and_tears_it_down(cloud):
    controller = cloud.launched()
    controller.cancel("r1", now=True)
    stray = cloud.provider.seed(cloud.settings.owner_tag, run_tag("r1"))
    cloud.reconcile()
    record = cloud.assert_torn_down("r1", "orphan")
    assert stray.id in record.resource_ids()


def test_expired_run_is_torn_down_while_the_controller_is_down(cloud):
    cloud.launched()
    cloud.clock.advance(2 * 3600)
    assert cloud.reconcile()["runs"] == [{"run_id": "r1", "state": "torn_down",
                                          "reason": "deadline"}]
    cloud.assert_torn_down("r1", "deadline")


def test_stuck_provisioning_run_is_torn_down_after_the_boot_timeout(cloud):
    cloud.provider.boot = False
    cloud.controller().launch("r1")
    cloud.reconcile()
    assert cloud.ledger.get("r1").state == "provisioning"
    cloud.clock.advance(301)
    cloud.reconcile()
    cloud.assert_torn_down("r1", "boot_failed")


def test_live_run_within_its_deadline_is_left_to_the_controller(cloud):
    cloud.launched()
    cloud.clock.advance(3600)
    assert cloud.reconcile() == {"runs": [], "orphans": [], "orphan_scan": "COMPLETE"}
    assert cloud.ledger.get("r1").state == "running"
    assert len(cloud.owned_ids("r1")) == 1


def test_reconciler_refuses_while_the_controller_holds_the_ledger_lock(cloud):
    cloud.provider.seed(cloud.settings.owner_tag, run_tag("lost"))
    with cloud.ledger.lock(), pytest.raises(LedgerBusy, match="LEDGER_BUSY"):
        cloud.reconcile()
    assert cloud.provider.deleted() == []


def test_failed_orphan_listing_deletes_nothing(cloud):
    cloud.provider.seed(cloud.settings.owner_tag, run_tag("lost"))
    cloud.provider.list_failures = 1
    assert cloud.reconcile()["orphan_scan"] == "LIST_FAILED"
    assert cloud.provider.deleted() == []


def test_owner_tag_without_a_run_tag_is_not_treated_as_an_orphan(cloud):
    unmatched = cloud.provider.seed(cloud.settings.owner_tag)
    cloud.reconcile()
    assert unmatched.id in cloud.provider.inventory
    cloud.assert_unrelated_survive()
