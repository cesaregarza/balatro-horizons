"""Delete, retry, verify, and the fail-closed launch gate."""

import json
from dataclasses import replace

import pytest
from cloud_support import Cloud

from balatro_horizons.cli import main

NEVER = 10**9


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def test_delete_that_fails_then_succeeds_is_retried_with_backoff(cloud):
    controller = cloud.launched()
    cloud.provider.delete_failures = 2
    start = cloud.clock()
    controller.cancel("r1", now=True)
    cloud.assert_torn_down("r1", "cancelled")
    assert len(cloud.provider.deleted()) == 3
    assert (cloud.clock() - start).total_seconds() == 1 + 2


def test_delete_that_never_succeeds_fails_closed_with_an_alert(cloud):
    controller = cloud.launched()
    cloud.provider.delete_failures = NEVER
    controller.cancel("r1", now=True)
    record = cloud.ledger.get("r1")
    assert record.state == "cleanup_unverified"
    assert [(a.run_id, a.code) for a in cloud.ledger.alerts()] == [("r1", "CLEANUP_UNVERIFIED")]
    refused = controller.launch("r2")
    assert refused["outcome"] == "refused" and "CLEANUP_UNVERIFIED" in refused["reasons"]
    assert cloud.ledger.get("r2") is None


def test_only_a_later_verified_absence_reopens_launches(cloud):
    controller = cloud.launched()
    cloud.provider.delete_failures = NEVER
    controller.cancel("r1", now=True)
    controller.tick()
    assert cloud.ledger.get("r1").state == "cleanup_unverified"
    assert len(cloud.ledger.alerts()) == 1
    cloud.provider.delete_failures = 0
    cloud.reconcile()
    cloud.assert_torn_down("r1", "cancelled")
    assert controller.launch("r2")["outcome"] == "launched"


def test_status_shows_the_unverified_alert_plainly(cloud, tmp_path, capsys):
    controller = cloud.launched()
    cloud.provider.delete_failures = NEVER
    controller.cancel("r1", now=True)
    config = tmp_path / "cloud.yaml"
    config.write_text(json.dumps(cloud.settings.model_dump()))
    code = main(["cloud", "status", "--data-dir", str(tmp_path), "--cloud-config", str(config)])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["launches"] == "REFUSED"
    assert "CLEANUP_UNVERIFIED" in report["refusals"]
    assert report["alerts"][0]["run_id"] == "r1"
    assert "New launches are refused" in report["alerts"][0]["message"]


def test_a_delete_that_reports_success_but_leaves_the_droplet_is_not_verified(cloud):
    controller = cloud.launched()
    cloud.provider.ghost_deletes = True
    controller.cancel("r1", now=True)
    assert cloud.ledger.get("r1").state == "cleanup_unverified"


def test_powering_off_never_counts_as_cleanup(cloud):
    controller = cloud.launched()
    droplet_id = cloud.owned_ids("r1")[0]
    cloud.provider.inventory[droplet_id] = replace(
        cloud.provider.inventory[droplet_id], status="off")
    cloud.provider.ghost_deletes = True
    controller.cancel("r1", now=True)
    assert cloud.ledger.get("r1").state == "cleanup_unverified"


def test_list_failure_is_retried_and_then_verified(cloud):
    controller = cloud.launched()
    cloud.provider.list_failures = 2
    controller.cancel("r1", now=True)
    cloud.assert_torn_down("r1", "cancelled")


def test_list_that_never_succeeds_cannot_verify_absence(cloud):
    controller = cloud.launched()
    cloud.provider.list_failures = NEVER
    controller.cancel("r1", now=True)
    assert cloud.owned_ids("r1") == []
    assert cloud.ledger.get("r1").state == "cleanup_unverified"


def test_unrecorded_droplet_carrying_the_run_tags_is_deleted_too(cloud):
    controller = cloud.launched()
    stray = cloud.provider.seed(cloud.settings.owner_tag, "bh-run:r1")
    controller.cancel("r1", now=True)
    cloud.assert_torn_down("r1", "cancelled")
    assert stray.id in cloud.ledger.get("r1").resource_ids()


@pytest.mark.parametrize("path", [
    "succeeded", "cancelled", "deadline", "heartbeat_lost", "boot_failed", "create_failed",
])
def test_untagged_and_foreign_resources_survive_every_path(cloud, path):
    if path in ("boot_failed", "create_failed"):
        cloud.provider.boot = False
        cloud.provider.partial_create = path == "create_failed"
        controller = cloud.controller()
        controller.launch("r1")
        cloud.clock.advance(301)
    else:
        controller = cloud.launched()
        cloud.clock.advance({"deadline": 7200, "heartbeat_lost": 121}.get(path, 0))
        if path == "deadline":
            cloud.channel.beat("r1", cloud.clock())
        if path == "succeeded":
            cloud.channel.finish("r1", cloud.clock())
        if path == "cancelled":
            controller.cancel("r1")
    controller.tick()
    cloud.reconcile()
    cloud.assert_torn_down("r1", path)
