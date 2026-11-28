"""Ambiguous creates, startup inventory, ownership before delete, and Pi restarts."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from cloud_support import Cloud, cloud_settings

from balatro_horizons.cli.cloud import status_report
from balatro_horizons.cloud.settings import run_tag

NEVER = 10**9
HOUR = 3600


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path / "cloud")


def lost_create(cloud, lag=3):
    """Create is accepted, the response is lost, and listings and the action log lag."""
    cloud.provider.partial_create = True
    cloud.provider.listing_lag = lag
    cloud.provider.pending_creates = True
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
    assert cloud.alert_codes() == ["CREATE_UNRESOLVED"]


def test_unresolved_create_resolves_once_its_droplet_becomes_visible(cloud):
    lost_create(cloud)
    controller = cloud.controller()
    for _ in range(3):
        controller.tick()
    record = cloud.assert_torn_down("r1", "create_failed")
    assert record.settled_usd is not None
    cloud.provider.partial_create = False
    assert controller.launch("r2")["outcome"] == "launched"


def test_server_error_on_create_stays_unresolved_however_long_reads_stay_empty(cloud):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "HTTP_503"
    controller = cloud.controller()
    assert controller.launch("r1")["outcome"] == "create_unresolved"
    for _ in range(24):
        cloud.clock.advance(HOUR)
        controller.tick()
        cloud.reconcile()
    assert cloud.ledger.get("r1").state == "create_unresolved"
    assert "CREATE_UNRESOLVED" in controller.launch("r2")["reasons"]
    assert cloud.provider.deleted() == []


def test_action_log_create_carrying_this_runs_tags_is_deleted_and_verified(cloud):
    cloud.provider.partial_create = True
    cloud.provider.listing_lag = NEVER
    controller = cloud.controller()
    controller.launch("r1")
    controller.tick()
    cloud.assert_torn_down("r1", "create_failed")
    assert len(cloud.provider.deleted()) == 1


def test_action_log_creates_with_foreign_tags_are_left_untouched(cloud):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "TRANSPORT_ERROR"
    controller = cloud.controller()
    controller.launch("r1")
    foreign = cloud.provider.seed("bh-owner:someone-else", run_tag("r1"))
    cloud.clock.advance(HOUR)
    controller.tick()
    assert cloud.ledger.get("r1").state == "create_unresolved"
    assert foreign.id in cloud.provider.inventory
    assert cloud.provider.deleted() == []


def test_unreadable_action_log_keeps_the_create_unresolved(cloud):
    lost_create(cloud, lag=NEVER)
    cloud.provider.action_failures = NEVER
    cloud.clock.advance(HOUR)
    cloud.controller().tick()
    cloud.reconcile()
    assert cloud.ledger.get("r1").state == "create_unresolved"
    assert "CREATE_UNRESOLVED" in cloud.controller().launch("r2")["reasons"]


def test_unlistable_inventory_keeps_the_create_unresolved(cloud):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "HTTP_502"
    cloud.controller().launch("r1")
    cloud.provider.list_failures = NEVER
    cloud.clock.advance(HOUR)
    cloud.controller().tick()
    assert cloud.ledger.get("r1").state == "create_unresolved"


def test_rejected_create_is_definite_and_settles_at_once(cloud):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "HTTP_422"
    assert cloud.controller().launch("r1")["outcome"] == "create_failed"
    cloud.assert_torn_down("r1", "create_failed")
    assert cloud.active_alerts() == []


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
    assert "UNMATCHED_DROPLET" in cloud.alert_codes()
    assert "UNMATCHED_DROPLET" in controller.launch("r2")["reasons"]
    # The run holding an ID it may not delete never settles, even once that ID reads 404.
    cloud.provider.inventory.pop(droplet_id)
    cloud.reconcile()
    controller.tick()
    record = cloud.ledger.get("r1")
    assert (record.state, record.settled_usd) == ("cleanup_unverified", None)
    assert {"UNMATCHED_DROPLET", "CONCURRENCY_LIMIT"} <= set(controller.launch("r2")["reasons"])


def test_restart_after_accepted_create_with_lost_id_cleans_without_a_second_create(cloud):
    cloud.provider.listing_lag = 2
    cloud.provider.pending_creates = True
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


def crash_after_the_delete(cloud):
    """The provider carries out the DELETE, then the controller dies before recording it."""
    controller = cloud.launched()
    cloud.clock.advance(1800)
    cloud.provider.crash_at.add("after_delete")
    with pytest.raises(BaseException, match="after_delete"):
        controller.cancel("r1", now=True)
    cloud.restarted().recover()
    return cloud.ledger.get("r1")


def test_restart_after_a_delete_whose_acknowledgment_was_lost_clears_on_the_action_log(cloud):
    record = crash_after_the_delete(cloud)
    assert (record.state, record.teardown_reason) == ("torn_down", "cancelled")
    assert record.settled_usd == pytest.approx(0.05)
    assert len(cloud.provider.deleted()) == 1
    assert record.resources[0].acknowledged_at is None


def test_restart_after_a_lost_acknowledgment_holds_while_the_log_shows_no_destroy(cloud):
    cloud.provider.log_destroys = False
    record = crash_after_the_delete(cloud)
    assert (record.state, record.settled_usd) == ("cleanup_unverified", None)
    assert len(cloud.provider.deleted()) == 1
    assert reserved_remaining(cloud) == pytest.approx(1.0 - cloud.settings.worst_case_usd)


# A slow action log: 50 pages at 30 seconds each, against a cleanup window of a minute.
SLOW_LOG = 50, 30


def test_restart_after_a_lost_acknowledgment_cuts_a_slow_log_read_off_at_the_window(cloud):
    cloud.provider.destroy_pages, cloud.provider.destroy_page_seconds = SLOW_LOG
    record = crash_after_the_delete(cloud)
    resource = record.resources[0]
    window_end = resource.delete_requested_at + timedelta(seconds=60)
    assert (record.state, record.verified_at, record.settled_usd) == (
        "cleanup_unverified", None, None)
    # Held at the window's end, not when the read would have finished, 25 minutes on.
    assert (record.history[-1].at, cloud.clock()) == (window_end, window_end)
    assert (resource.destroy_evidence, getattr(resource, "destroy_check", None),
            resource.destroy_checked_at) == (None, "timed_out", window_end)
    assert "CLEANUP_UNVERIFIED" in cloud.alert_codes()


def test_restart_after_a_lost_acknowledgment_clears_on_a_destroy_read_in_time(cloud):
    # One page, read in 30 seconds, within the window.
    cloud.provider.destroy_pages, cloud.provider.destroy_page_seconds = 1, 30
    record = crash_after_the_delete(cloud)
    resource = record.resources[0]
    assert (record.state, resource.acknowledged_at, resource.destroy_evidence.status) == (
        "torn_down", None, "completed")
    assert record.verified_at == resource.delete_requested_at + timedelta(seconds=30)


@pytest.mark.parametrize("log", ["no destroy", "failed", "too slow"])
def test_a_lost_acknowledgment_never_clears_on_a_404_a_failed_read_or_a_read_that_ran_out(
        cloud, log):
    if log == "no destroy":
        cloud.provider.log_destroys = False
    elif log == "failed":
        cloud.provider.action_failures = NEVER
    else:
        cloud.provider.destroy_pages, cloud.provider.destroy_page_seconds = SLOW_LOG
    crash_after_the_delete(cloud)
    for _ in range(3):
        cloud.controller().tick()
        cloud.reconcile()
    record = cloud.ledger.get("r1")
    assert (record.state, record.settled_usd) == ("cleanup_unverified", None)
    assert getattr(record.resources[0], "destroy_check", None) == {
        "no destroy": "unknown", "failed": "failed", "too slow": "timed_out"}[log]
    assert "CLEANUP_UNVERIFIED" in cloud.controller().launch("r2")["reasons"]


def test_each_retry_of_a_held_run_reads_the_log_within_a_fresh_window(cloud):
    cloud.provider.destroy_pages, cloud.provider.destroy_page_seconds = SLOW_LOG
    crash_after_the_delete(cloud)
    cloud.clock.advance(HOUR)
    window_end = cloud.clock() + timedelta(seconds=60)
    cloud.reconcile()
    record = cloud.ledger.get("r1")
    resource = record.resources[0]
    assert (record.state, getattr(resource, "destroy_check", None),
            resource.destroy_checked_at, cloud.clock()) == (
        "cleanup_unverified", "timed_out", window_end, window_end)
    # Once the log reads in time, the next retry clears the run.
    cloud.provider.destroy_pages = 1
    cloud.reconcile()
    record = cloud.assert_torn_down("r1", "cancelled")
    resource = record.resources[0]
    # Then the lookup runs. Only it stamps a time after `verified_at`: one page, 30 s on.
    assert (resource.destroy_check, resource.destroy_checked_at) == (
        "found", record.verified_at + timedelta(seconds=30))


# Run r1 as an earlier version wrote it, byte for byte, with `not_found` for a lookup that
# finished with no match. That version reaches it when a first pass whose DELETE answer was
# lost reads the log and records the miss, and a later pass's DELETE is acknowledged before
# the window runs out. The Droplet still reads back when the window ends, so the run is held.
EARLIER_HELD_RUN = (
    '{"actions_seen":0,"address":"192.0.2.245","cancel_requested":true,'
    '"create_attempted_at":"2026-11-24T12:00:00Z","create_rejected":false,'
    '"created_at":"2026-11-24T12:00:00Z","deadline":"2026-11-24T14:00:00Z",'
    '"grace_from":"2026-11-24T12:00:00Z","history":[{"at":"2026-11-24T12:00:00Z","reason":null,'
    '"state":"provisioning"},{"at":"2026-11-24T12:00:00Z","reason":null,"state":"running"},'
    '{"at":"2026-11-24T12:00:00Z","reason":"cancelled","state":"tearing_down"},'
    '{"at":"2026-11-24T12:01:03Z","reason":"cancelled","state":"cleanup_unverified"}],'
    '"last_ssh_failure":null,"released":[],"reserved_usd":0.2,'
    '"resources":[{"acknowledged_at":"2026-11-24T12:01:03Z",'
    '"delete_requested_at":"2026-11-24T12:01:03Z","destroy_check":"not_found",'
    '"destroy_checked_at":"2026-11-24T12:00:00Z","destroy_evidence":null,"id":1006,'
    '"kind":"droplet"}],"results_copy":null,"run_id":"r1","settled_usd":null,'
    '"state":"cleanup_unverified","teardown_reason":"cancelled",'
    '"teardown_started_at":"2026-11-24T12:00:00Z","verified_at":null,'
    '"worker_started_at":"2026-11-24T12:00:00Z","worker_status":{"actions":0,"evidence":{},'
    '"finished_at":null,"observed":{"kernel":"6.8.0-test"},"outcome":null,"reason":null,'
    '"stage":"play","started_at":{"play":"2026-11-24T12:04:00Z"},"state":"running"}}\n'
)


def earlier_held_run(cloud):
    """Store EARLIER_HELD_RUN as the ledger's only record, then move the clock an hour on.
    Returns the record parsed as plain JSON, not through the model."""
    cloud.ledger.runs_dir.mkdir(parents=True)
    (cloud.ledger.runs_dir / "r1.json").write_text(EARLIER_HELD_RUN)
    cloud.clock.advance(HOUR)
    return json.loads(EARLIER_HELD_RUN)


def test_an_earlier_held_run_loads_as_stored_with_its_miss_read_as_unknown(cloud):
    expected = earlier_held_run(cloud)
    assert expected["resources"][0]["destroy_check"] == "not_found"
    expected["resources"][0]["destroy_check"] = "unknown"
    loaded = [cloud.ledger.get("r1"), *cloud.ledger.runs()]
    assert [record.model_dump(mode="json") for record in loaded] == [expected, expected]


def test_status_shows_an_earlier_held_run_with_its_reservation(cloud):
    """Status takes no provider: it reads the ledger alone."""
    stored = earlier_held_run(cloud)
    report = status_report(cloud.ledger, cloud.settings, cloud.clock())
    (run,) = report["runs"]
    assert (run["run_id"], run["state"], run["reserved_usd"], run["cost_usd"]) == (
        "r1", "cleanup_unverified", stored["reserved_usd"], stored["reserved_usd"])
    assert [(a["code"], a["run_id"]) for a in report["alerts"]] == [("CLEANUP_UNVERIFIED", "r1")]
    # As for any held run, it keeps its slot as well as its reservation.
    assert report["refusals"] == ["CLEANUP_UNVERIFIED", "CONCURRENCY_LIMIT"]


class RefusingProvider:
    """Refuses every call, as an unreachable provider would, and records each attempt."""

    def __init__(self):
        self.attempts = []

    def __getattr__(self, name):
        def refuse(*args, **kwargs):
            self.attempts.append(name)
            raise ConnectionRefusedError(name)

        return refuse


def test_reconcile_changes_nothing_in_an_earlier_held_run_while_every_call_is_refused(cloud):
    """Reconcile always reads the inventory, so it can't run with no provider call at all.
    The refusing provider is the closest proof that loading the record needs no provider."""
    stored = earlier_held_run(cloud)
    cloud.provider = RefusingProvider()
    assert cloud.reconcile() == {
        "runs": [{"run_id": "r1", "state": "cleanup_unverified", "reason": "cancelled"}],
        "orphans": [], "unmatched": [], "orphan_scan": "LIST_FAILED"}
    # The record is not rewritten. The run stays held, with its ID, its acknowledgment and
    # its reservation.
    assert (cloud.ledger.runs_dir / "r1.json").read_text() == EARLIER_HELD_RUN
    record = cloud.ledger.get("r1")
    assert (record.state, [(r.id, r.acknowledged_at is not None) for r in record.resources],
            record.reserved_usd) == (
        "cleanup_unverified", [(stored["resources"][0]["id"], True)], stored["reserved_usd"])
    assert "delete_droplet" not in cloud.provider.attempts
    assert set(cloud.provider.attempts) == {"list_droplets"}


def test_running_worker_kept_only_within_its_original_deadline_after_restart(cloud):
    cloud.launched()
    deadline = cloud.ledger.get("r1").deadline
    stale = cloud.provider.seed(cloud.settings.owner_tag, run_tag("old"))
    cloud.clock.advance(2 * 3600 - 60)
    cloud.channel.progress("r1")
    restarted = cloud.restarted()
    restarted.recover()
    assert stale.id not in cloud.provider.inventory
    assert cloud.ledger.get("r1").state == "running"
    assert cloud.ledger.get("r1").deadline == deadline
    cloud.clock.advance(61)
    cloud.channel.progress("r1")
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


def test_rejected_create_whose_teardown_is_unverified_is_never_held_as_unresolved(
    cloud, monkeypatch,
):
    cloud.provider.fail_create = True
    cloud.provider.fail_create_code = "HTTP_422"
    listings = {"calls": 0}
    list_droplets = cloud.provider.list_droplets

    def failing_after_the_inventory_read(tag):
        listings["calls"] += 1
        if listings["calls"] > 1:
            raise RuntimeError("listing unavailable")
        return list_droplets(tag)

    monkeypatch.setattr(cloud.provider, "list_droplets", failing_after_the_inventory_read)
    assert cloud.controller().launch("r1")["state"] == "cleanup_unverified"
    monkeypatch.undo()
    cloud.restarted().tick()
    cloud.assert_torn_down("r1", "create_failed")
    assert "create_unresolved" not in [t.state for t in cloud.ledger.get("r1").history]
    assert cloud.active_alerts() == []
