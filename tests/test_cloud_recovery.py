"""Ambiguous creates, startup inventory, ownership before delete, and Pi restarts."""

from dataclasses import replace

import pytest
from cloud_support import Cloud, cloud_settings

from balatro_horizons.cli.cloud import status_report
from balatro_horizons.cloud.settings import run_tag

NEVER = 10**9
SETTLE = 600


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def lost_create(cloud, lag=3):
    """Create is accepted, the response is lost, and listings lag behind."""
    cloud.provider.partial_create = True
    cloud.provider.listing_lag = lag
    return cloud.controller().launch("r1")


def reserved_remaining(cloud):
    runs = cloud.ledger.runs()
    spent = sum(r.settled_usd if r.state == "torn_down" else r.reserved_usd for r in runs)
    return cloud.settings.infrastructure_budget_usd - spent


def test_ambiguous_create_with_delayed_visibility_stays_unresolved_and_blocks(cloud):
    assert lost_create(cloud)["outcome"] == "create_unresolved"
    assert cloud.ledger.get("r1").state == "create_unresolved"
    assert len(cloud.owned_ids("r1")) == 1
    refused = cloud.controller().launch("r2")
    assert "CREATE_UNRESOLVED" in refused["reasons"]
    assert len(cloud.creates()) == 1
    assert [a.code for a in cloud.ledger.alerts()] == ["CREATE_UNRESOLVED"]


def test_unresolved_create_resolves_once_its_droplet_becomes_visible(cloud):
    lost_create(cloud)
    controller = cloud.controller()
    for _ in range(3):
        controller.tick()
    record = cloud.assert_torn_down("r1", "create_failed")
    assert record.settled_usd is not None
    cloud.provider.partial_create = False
    assert controller.launch("r2")["outcome"] == "launched"


def test_server_error_on_create_waits_for_the_settle_window_and_the_action_log(cloud):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "HTTP_503"
    controller = cloud.controller()
    assert controller.launch("r1")["outcome"] == "create_unresolved"
    cloud.clock.advance(SETTLE - 1)
    controller.tick()
    assert cloud.ledger.get("r1").state == "create_unresolved"
    cloud.clock.advance(2)
    controller.tick()
    cloud.assert_torn_down("r1", "create_failed")


def test_action_log_create_carrying_this_runs_tags_is_deleted_and_verified(cloud):
    lost_create(cloud, lag=NEVER)
    controller = cloud.controller()
    controller.tick()
    assert cloud.ledger.get("r1").state == "create_unresolved"
    cloud.clock.advance(SETTLE + 1)
    controller.tick()
    cloud.assert_torn_down("r1", "create_failed")
    assert len(cloud.provider.deleted()) == 1


def test_action_log_creates_with_foreign_tags_are_left_untouched(cloud):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "TRANSPORT_ERROR"
    controller = cloud.controller()
    controller.launch("r1")
    foreign = cloud.provider.seed("bh-owner:someone-else", run_tag("r1"))
    assert cloud.ledger.get("r1").state == "create_unresolved"
    cloud.clock.advance(SETTLE + 1)
    controller.tick()
    cloud.assert_torn_down("r1", "create_failed")
    assert foreign.id in cloud.provider.inventory
    assert cloud.provider.deleted() == []


def test_unreadable_action_log_keeps_the_create_unresolved(cloud):
    lost_create(cloud, lag=NEVER)
    cloud.provider.action_failures = NEVER
    cloud.clock.advance(SETTLE + 1)
    cloud.controller().tick()
    cloud.reconcile()
    assert cloud.ledger.get("r1").state == "create_unresolved"
    assert "CREATE_UNRESOLVED" in cloud.controller().launch("r2")["reasons"]


def test_unlistable_inventory_keeps_the_create_unresolved(cloud):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "HTTP_502"
    cloud.controller().launch("r1")
    cloud.provider.list_failures = NEVER
    cloud.clock.advance(SETTLE + 1)
    cloud.controller().tick()
    assert cloud.ledger.get("r1").state == "create_unresolved"


def test_rejected_create_is_definite_and_needs_no_settle_window(cloud):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "HTTP_422"
    assert cloud.controller().launch("r1")["outcome"] == "create_failed"
    cloud.assert_torn_down("r1", "create_failed")
    assert not cloud.ledger.alerts()


def test_startup_cleans_a_visible_former_worker_before_any_launch(cloud):
    former = cloud.provider.seed(cloud.settings.owner_tag, run_tag("old"))
    controller = cloud.restarted()
    controller.recover()
    assert former.id not in cloud.provider.inventory
    cloud.assert_torn_down("old", "orphan")


def test_launch_reconciles_owned_inventory_before_creating(cloud):
    former = cloud.provider.seed(cloud.settings.owner_tag, run_tag("old"))
    assert cloud.controller().launch("r1")["outcome"] == "launched"
    calls = [call[0:2] for call in cloud.provider.calls if call[0] in ("delete", "create")]
    assert calls[0] == ("delete", former.id)
    assert calls[-1][0] == "create"


def test_unlistable_inventory_refuses_launch_and_status_says_why(cloud):
    cloud.provider.list_failures = NEVER
    refused = cloud.controller().launch("r1")
    assert refused.get("reasons") == ["INVENTORY_UNVERIFIED"]
    assert cloud.creates() == []
    report = status_report(cloud.ledger, cloud.settings, cloud.clock())
    assert "INVENTORY_UNVERIFIED" in report["refusals"]


def test_known_id_whose_tags_were_removed_is_not_deleted_and_fails_closed(cloud):
    controller = cloud.launched()
    droplet_id = cloud.owned_ids("r1")[0]
    stripped = replace(cloud.provider.inventory[droplet_id], tags=())
    cloud.provider.inventory[droplet_id] = stripped
    controller.cancel("r1", now=True)
    assert droplet_id in cloud.provider.inventory
    assert droplet_id not in cloud.provider.deleted()
    assert "UNMATCHED_DROPLET" in [a.code for a in cloud.ledger.alerts()]
    assert "UNMATCHED_DROPLET" in controller.launch("r2")["reasons"]
    cloud.provider.inventory.pop(droplet_id)
    cloud.reconcile()
    assert controller.launch("r2")["outcome"] == "launched"


def test_restart_after_accepted_create_with_lost_id_cleans_without_a_second_create(cloud):
    cloud.provider.listing_lag = 2
    cloud.provider.crash_at.add("after_create")
    with pytest.raises(BaseException, match="after_create"):
        cloud.controller().launch("r1")
    restarted = cloud.restarted()
    restarted.recover()
    assert cloud.ledger.get("r1").state == "create_unresolved"
    assert reserved_remaining(cloud) == pytest.approx(1.0 - 0.2)
    for _ in range(3):
        restarted.tick()
    cloud.assert_torn_down("r1", "restart_while_provisioning")
    assert len(cloud.creates()) == 1


def test_restart_after_delete_issued_reverifies_without_double_counting(cloud):
    controller = cloud.launched()
    cloud.clock.advance(1800)
    cloud.provider.crash_at.add("after_delete")
    with pytest.raises(BaseException, match="after_delete"):
        controller.cancel("r1", now=True)
    cloud.restarted().recover()
    record = cloud.assert_torn_down("r1", "cancelled")
    assert len(cloud.provider.deleted()) == 1
    assert reserved_remaining(cloud) == pytest.approx(1.0 - record.settled_usd)


def test_running_worker_kept_only_within_its_original_deadline_after_restart(cloud):
    cloud.launched()
    deadline = cloud.ledger.get("r1").deadline
    stale = cloud.provider.seed(cloud.settings.owner_tag, run_tag("old"))
    cloud.clock.advance(2 * 3600 - 60)
    cloud.channel.beat("r1", cloud.clock())
    restarted = cloud.restarted()
    restarted.recover()
    assert stale.id not in cloud.provider.inventory
    assert cloud.ledger.get("r1").state == "running"
    assert cloud.ledger.get("r1").deadline == deadline
    cloud.clock.advance(61)
    cloud.channel.beat("r1", cloud.clock())
    restarted.tick()
    cloud.assert_torn_down("r1", "deadline")


def test_provider_unreachable_at_startup_blocks_launches_and_shows_unverified(tmp_path):
    cloud = Cloud(tmp_path / "cloud", settings=cloud_settings())
    cloud.provider.boot = False
    cloud.controller().launch("r1")
    cloud.provider.list_failures = NEVER
    cloud.provider.delete_failures = NEVER
    restarted = cloud.restarted()
    restarted.recover()
    assert cloud.ledger.get("r1").state == "cleanup_unverified"
    reasons = restarted.launch("r2")["reasons"]
    assert {"CLEANUP_UNVERIFIED", "INVENTORY_UNVERIFIED"} <= set(reasons)
    cloud.assert_unrelated_survive()
